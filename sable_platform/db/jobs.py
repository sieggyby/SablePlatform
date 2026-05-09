"""Job and step management helpers for sable.db."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.errors import SableError, MAX_RETRIES_EXCEEDED


def create_job(
    conn: Connection,
    org_id: str,
    job_type: str,
    config: dict | None = None,
) -> str:
    job_id = uuid.uuid4().hex
    conn.execute(
        text(
            "INSERT INTO jobs (job_id, org_id, job_type, status, config_json)"
            " VALUES (:job_id, :org_id, :job_type, 'pending', :config_json)"
        ),
        {"job_id": job_id, "org_id": org_id, "job_type": job_type, "config_json": json.dumps(config or {})},
    )
    conn.commit()
    return job_id


def add_step(
    conn: Connection,
    job_id: str,
    step_name: str,
    step_order: int = 0,
    input_data: dict | None = None,
) -> int:
    row = conn.execute(
        text(
            "INSERT INTO job_steps (job_id, step_name, step_order, status, input_json)"
            " VALUES (:job_id, :step_name, :step_order, 'pending', :input_json)"
            " RETURNING step_id"
        ),
        {"job_id": job_id, "step_name": step_name, "step_order": step_order, "input_json": json.dumps(input_data or {})},
    ).fetchone()
    conn.commit()
    if row is None:
        raise RuntimeError("INSERT INTO job_steps did not return step_id")
    return row[0]


def start_step(conn: Connection, step_id: int) -> None:
    conn.execute(
        text(
            "UPDATE job_steps"
            " SET status='running', started_at=CURRENT_TIMESTAMP"
            " WHERE step_id=:step_id"
        ),
        {"step_id": step_id},
    )
    conn.commit()


def complete_step(conn: Connection, step_id: int, output: dict | None = None) -> None:
    conn.execute(
        text(
            "UPDATE job_steps"
            " SET status='completed', completed_at=CURRENT_TIMESTAMP, output_json=:output_json"
            " WHERE step_id=:step_id"
        ),
        {"output_json": json.dumps(output or {}), "step_id": step_id},
    )
    conn.commit()


def fail_step(conn: Connection, step_id: int, error: str | None = None) -> None:
    conn.execute(
        text(
            "UPDATE job_steps"
            " SET status='failed', retries = retries + 1, error=:error"
            " WHERE step_id=:step_id"
        ),
        {"error": error, "step_id": step_id},
    )
    conn.commit()


def get_job(conn: Connection, job_id: str):
    return conn.execute(
        text("SELECT * FROM jobs WHERE job_id=:job_id"),
        {"job_id": job_id},
    ).fetchone()


def get_resumable_steps(conn: Connection, job_id: str) -> list:
    """Return all steps for the job ordered by step_order."""
    return conn.execute(
        text("SELECT * FROM job_steps WHERE job_id=:job_id ORDER BY step_order"),
        {"job_id": job_id},
    ).fetchall()


def claim_next_job(
    conn: Connection,
    job_type: str,
    worker_id: str,
    stale_after_minutes: int = 10,
) -> dict | None:
    """Atomically claim the oldest pending job of *job_type*, OR reclaim a stale running one.

    A job is "stale" when status='running' but updated_at is older than
    *stale_after_minutes* — implies the worker that claimed it crashed.

    Returns None if no claimable job. Otherwise returns a dict:
        {"job_id": str, "config_json": dict, "org_id": str}

    Atomicity:
      - Postgres: SELECT ... FOR UPDATE SKIP LOCKED inside an UPDATE subquery.
      - SQLite: UPDATE ... WHERE job_id = (SELECT ... LIMIT 1) RETURNING is one
        statement, serialized by SQLite's database-level write lock — two
        concurrent claimers hit the busy_timeout retry, only one wins.

    Both paths bump jobs.updated_at and stamp jobs.worker_id.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=stale_after_minutes)
    ).strftime("%Y-%m-%d %H:%M:%S")

    if conn.dialect.name == "postgresql":
        sql = text(
            "UPDATE jobs"
            " SET status='running',"
            "     worker_id=:worker_id,"
            "     updated_at=now()"
            " WHERE job_id = ("
            "     SELECT job_id FROM jobs"
            "     WHERE job_type = :job_type"
            "       AND ("
            "           status = 'pending'"
            "           OR (status = 'running' AND updated_at < :cutoff)"
            "       )"
            "     ORDER BY created_at"
            "     FOR UPDATE SKIP LOCKED"
            "     LIMIT 1"
            " )"
            " RETURNING job_id, config_json, org_id"
        )
    else:
        sql = text(
            "UPDATE jobs"
            " SET status='running',"
            "     worker_id=:worker_id,"
            "     updated_at=datetime('now')"
            " WHERE job_id = ("
            "     SELECT job_id FROM jobs"
            "     WHERE job_type = :job_type"
            "       AND ("
            "           status = 'pending'"
            "           OR (status = 'running' AND updated_at < :cutoff)"
            "       )"
            "     ORDER BY created_at"
            "     LIMIT 1"
            " )"
            " RETURNING job_id, config_json, org_id"
        )

    row = conn.execute(
        sql,
        {"worker_id": worker_id, "job_type": job_type, "cutoff": cutoff},
    ).fetchone()
    conn.commit()
    if row is None:
        return None
    return {
        "job_id": row[0],
        "config_json": json.loads(row[1] or "{}"),
        "org_id": row[2],
    }


def complete_job(conn: Connection, job_id: str, result: dict | None = None) -> None:
    """Mark *job_id* done.  Sets completed_at + result_json, bumps updated_at."""
    conn.execute(
        text(
            "UPDATE jobs"
            " SET status='done',"
            "     completed_at=CURRENT_TIMESTAMP,"
            "     updated_at=CURRENT_TIMESTAMP,"
            "     result_json=:result_json"
            " WHERE job_id=:job_id"
        ),
        {"result_json": json.dumps(result or {}), "job_id": job_id},
    )
    conn.commit()


def fail_job(conn: Connection, job_id: str, error: str) -> None:
    """Mark *job_id* failed with *error* recorded in error_message."""
    conn.execute(
        text(
            "UPDATE jobs"
            " SET status='failed',"
            "     completed_at=CURRENT_TIMESTAMP,"
            "     updated_at=CURRENT_TIMESTAMP,"
            "     error_message=:error"
            " WHERE job_id=:job_id"
        ),
        {"error": error, "job_id": job_id},
    )
    conn.commit()


def release_job(conn: Connection, job_id: str) -> None:
    """Release a running job back to pending, clearing worker_id.

    Used when the worker decides to defer (e.g. a step's next_retry_at is in
    the future) rather than crash.  Bumps updated_at so the released job
    sorts after still-pending jobs that have been waiting longer.
    """
    conn.execute(
        text(
            "UPDATE jobs"
            " SET status='pending',"
            "     worker_id=NULL,"
            "     updated_at=CURRENT_TIMESTAMP"
            " WHERE job_id=:job_id"
        ),
        {"job_id": job_id},
    )
    conn.commit()


def defer_step(conn: Connection, step_id: int, retry_at: str) -> None:
    """Set job_steps.next_retry_at on a step (used for 429 deferred retry).

    *retry_at* is an ISO-8601 UTC timestamp string.  The worker only attempts
    a step when next_retry_at IS NULL or <= now.
    """
    conn.execute(
        text(
            "UPDATE job_steps"
            " SET next_retry_at=:retry_at"
            " WHERE step_id=:step_id"
        ),
        {"retry_at": retry_at, "step_id": step_id},
    )
    conn.commit()


def resume_job(conn: Connection, job_id: str, max_retries: int = 2) -> list[dict]:
    """
    Run the resume state machine for all steps in the job.

    Returns a list of dicts: [{step_name, action}] where action is one of:
      'skip'   — step already completed
      'retry'  — step was failed but retries < max_retries; set back to pending
      'wait'   — step is awaiting_input
      'run'    — step is pending; set to running
    """
    steps = get_resumable_steps(conn, job_id)
    actions = []

    for step in steps:
        status = step["status"]
        retries = step["retries"]

        if status == "completed":
            actions.append({"step_name": step["step_name"], "action": "skip"})

        elif status == "failed":
            if retries < max_retries:
                conn.execute(
                    text("UPDATE job_steps SET status='pending' WHERE step_id=:step_id"),
                    {"step_id": step["step_id"]},
                )
                conn.commit()
                actions.append({"step_name": step["step_name"], "action": "retry"})
            else:
                raise SableError(
                    MAX_RETRIES_EXCEEDED,
                    f"Step '{step['step_name']}' (job {job_id}) has exhausted {retries} retries",
                )

        elif status == "awaiting_input":
            actions.append({"step_name": step["step_name"], "action": "wait"})

        else:
            conn.execute(
                text("UPDATE job_steps SET status='running' WHERE step_id=:step_id"),
                {"step_id": step["step_id"]},
            )
            conn.commit()
            actions.append({"step_name": step["step_name"], "action": "run"})

    return actions
