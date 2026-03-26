"""Job and step management helpers for sable.db."""
from __future__ import annotations

import json
import sqlite3
import uuid

from sable_platform.errors import SableError, MAX_RETRIES_EXCEEDED


def create_job(
    conn: sqlite3.Connection,
    org_id: str,
    job_type: str,
    config: dict | None = None,
) -> str:
    job_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO jobs (job_id, org_id, job_type, status, config_json)
        VALUES (?, ?, ?, 'pending', ?)
        """,
        (job_id, org_id, job_type, json.dumps(config or {})),
    )
    conn.commit()
    return job_id


def add_step(
    conn: sqlite3.Connection,
    job_id: str,
    step_name: str,
    step_order: int = 0,
    input_data: dict | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO job_steps (job_id, step_name, step_order, status, input_json)
        VALUES (?, ?, ?, 'pending', ?)
        """,
        (job_id, step_name, step_order, json.dumps(input_data or {})),
    )
    conn.commit()
    return cursor.lastrowid


def start_step(conn: sqlite3.Connection, step_id: int) -> None:
    conn.execute(
        """
        UPDATE job_steps
        SET status='running', started_at=datetime('now')
        WHERE step_id=?
        """,
        (step_id,),
    )
    conn.commit()


def complete_step(conn: sqlite3.Connection, step_id: int, output: dict | None = None) -> None:
    conn.execute(
        """
        UPDATE job_steps
        SET status='completed', completed_at=datetime('now'), output_json=?
        WHERE step_id=?
        """,
        (json.dumps(output or {}), step_id),
    )
    conn.commit()


def fail_step(conn: sqlite3.Connection, step_id: int, error: str | None = None) -> None:
    conn.execute(
        """
        UPDATE job_steps
        SET status='failed', retries = retries + 1, error=?
        WHERE step_id=?
        """,
        (error, step_id),
    )
    conn.commit()


def get_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    return conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()


def get_resumable_steps(conn: sqlite3.Connection, job_id: str) -> list[sqlite3.Row]:
    """Return all steps for the job ordered by step_order."""
    return conn.execute(
        "SELECT * FROM job_steps WHERE job_id=? ORDER BY step_order",
        (job_id,),
    ).fetchall()


def resume_job(conn: sqlite3.Connection, job_id: str, max_retries: int = 2) -> list[dict]:
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
                    "UPDATE job_steps SET status='pending' WHERE step_id=?",
                    (step["step_id"],),
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
                "UPDATE job_steps SET status='running' WHERE step_id=?",
                (step["step_id"],),
            )
            conn.commit()
            actions.append({"step_name": step["step_name"], "action": "run"})

    return actions
