"""DB persistence for workflow_runs, workflow_steps, and workflow_events."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid

from sqlalchemy.exc import IntegrityError as SAIntegrityError

from sable_platform.errors import (
    redact_error,
    SableError,
    STEP_EXECUTION_ERROR,
    WORKFLOW_ALREADY_RUNNING,
    WORKFLOW_NOT_FOUND,
)
from sable_platform.webhooks.dispatch import dispatch_event

log = logging.getLogger(__name__)
_ACTIVE_RUN_LOCK_INDEX = "idx_workflow_runs_active_lock"


def _get_operator_id() -> str:
    """Read operator identity from env. Returns 'unknown' if unset."""
    return os.environ.get("SABLE_OPERATOR_ID", "unknown")


def _is_active_run_lock_error(exc: sqlite3.IntegrityError) -> bool:
    msg = str(exc)
    return (
        _ACTIVE_RUN_LOCK_INDEX in msg
        or "workflow_runs.org_id, workflow_runs.workflow_name" in msg
    )


def create_workflow_run(
    conn: sqlite3.Connection,
    org_id: str,
    workflow_name: str,
    workflow_version: str,
    config: dict,
    step_fingerprint: str | None = None,
) -> str:
    run_id = uuid.uuid4().hex
    operator_id = _get_operator_id()
    try:
        conn.execute(
            """
            INSERT INTO workflow_runs
                (run_id, org_id, workflow_name, workflow_version, status, config_json, step_fingerprint, operator_id)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (run_id, org_id, workflow_name, workflow_version, json.dumps(config), step_fingerprint, operator_id),
        )
    except (sqlite3.IntegrityError, SAIntegrityError) as exc:
        if _is_active_run_lock_error(exc):
            raise SableError(
                WORKFLOW_ALREADY_RUNNING,
                f"Workflow '{workflow_name}' already has an active run for org '{org_id}'.",
            ) from exc
        raise
    conn.commit()
    return run_id


def start_workflow_run(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute(
        "UPDATE workflow_runs SET status='running', started_at=datetime('now') WHERE run_id=?",
        (run_id,),
    )
    conn.commit()


def complete_workflow_run(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute(
        "UPDATE workflow_runs SET status='completed', completed_at=datetime('now') WHERE run_id=?",
        (run_id,),
    )
    conn.commit()


def fail_workflow_run(conn: sqlite3.Connection, run_id: str, error: str) -> None:
    conn.execute(
        "UPDATE workflow_runs SET status='failed', completed_at=datetime('now'), error=? WHERE run_id=?",
        (redact_error(error), run_id),
    )
    conn.commit()


def unlock_workflow_run(conn: sqlite3.Connection, run_id: str) -> bool:
    """Force-fail a stuck workflow run. Returns True if a row was updated."""
    cursor = conn.execute(
        """
        UPDATE workflow_runs SET status='failed', completed_at=datetime('now'),
               error='manually unlocked via CLI'
        WHERE run_id=? AND status IN ('pending', 'running')
        """,
        (run_id,),
    )
    conn.commit()
    return cursor.rowcount > 0


def create_workflow_step(
    conn: sqlite3.Connection,
    run_id: str,
    step_name: str,
    step_index: int,
    input_data: dict,
) -> str:
    step_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO workflow_steps (step_id, run_id, step_name, step_index, status, input_json)
        VALUES (?, ?, ?, ?, 'pending', ?)
        """,
        (step_id, run_id, step_name, step_index, json.dumps(input_data)),
    )
    conn.commit()
    return step_id


def start_workflow_step(conn: sqlite3.Connection, step_id: str) -> None:
    conn.execute(
        "UPDATE workflow_steps SET status='running', started_at=datetime('now') WHERE step_id=?",
        (step_id,),
    )
    conn.commit()


def complete_workflow_step(conn: sqlite3.Connection, step_id: str, output: dict) -> None:
    conn.execute(
        """
        UPDATE workflow_steps
        SET status='completed', completed_at=datetime('now'), output_json=?
        WHERE step_id=?
        """,
        (json.dumps(output), step_id),
    )
    conn.commit()


def skip_workflow_step(conn: sqlite3.Connection, step_id: str, reason: str) -> None:
    conn.execute(
        """
        UPDATE workflow_steps
        SET status='skipped', completed_at=datetime('now'), output_json=?
        WHERE step_id=?
        """,
        (json.dumps({"_skip_reason": reason}), step_id),
    )
    conn.commit()


def fail_workflow_step(conn: sqlite3.Connection, step_id: str, error: str) -> None:
    conn.execute(
        """
        UPDATE workflow_steps
        SET status='failed', completed_at=datetime('now'), retries=retries+1, error=?
        WHERE step_id=?
        """,
        (redact_error(error), step_id),
    )
    conn.commit()


def mark_timed_out_runs(conn: sqlite3.Connection, hours: int = 6) -> list[str]:
    """Mark workflow_runs stuck in 'running' for >hours as 'timed_out'."""
    rows = conn.execute(
        """
        SELECT run_id FROM workflow_runs
        WHERE status='running'
          AND started_at < datetime('now', ? || ' hours')
        """,
        (f"-{hours}",),
    ).fetchall()
    run_ids = [r["run_id"] for r in rows]
    for run_id in run_ids:
        conn.execute(
            "UPDATE workflow_runs SET status='timed_out', completed_at=datetime('now') WHERE run_id=?",
            (run_id,),
        )
    conn.commit()
    return run_ids


def cancel_workflow_run(conn: sqlite3.Connection, run_id: str) -> None:
    """Mark a non-terminal run as cancelled. Raises SableError on already-terminal status."""
    row = conn.execute("SELECT status FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise SableError(WORKFLOW_NOT_FOUND, f"Workflow run '{run_id}' not found")
    if row["status"] in ("completed", "failed", "cancelled", "timed_out"):
        raise SableError(STEP_EXECUTION_ERROR, f"Cannot cancel run '{run_id}': already {row['status']}")
    conn.execute(
        "UPDATE workflow_runs SET status='cancelled', completed_at=datetime('now') WHERE run_id=?",
        (run_id,),
    )
    conn.commit()


def reset_workflow_step_for_retry(conn: sqlite3.Connection, step_id: str) -> None:
    conn.execute(
        "UPDATE workflow_steps SET status='pending', started_at=NULL, completed_at=NULL, error=NULL WHERE step_id=?",
        (step_id,),
    )
    conn.commit()


def emit_workflow_event(
    conn: sqlite3.Connection,
    run_id: str,
    event_type: str,
    step_id: str | None = None,
    payload: dict | None = None,
) -> None:
    event_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO workflow_events (event_id, run_id, step_id, event_type, payload_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_id, run_id, step_id, event_type, json.dumps(payload or {})),
    )
    conn.commit()

    # Dispatch to webhooks (best-effort, never blocks)
    try:
        run_row = conn.execute(
            "SELECT org_id, workflow_name FROM workflow_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if run_row and run_row["org_id"]:
            webhook_payload = dict(payload or {})
            webhook_payload["run_id"] = run_id
            webhook_payload["workflow_name"] = run_row["workflow_name"]
            dispatch_event(conn, f"workflow.{event_type}", run_row["org_id"], webhook_payload)
    except Exception as e:
        log.warning("Webhook dispatch failed during workflow event: %s", e)


def get_workflow_run(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()


def get_workflow_steps(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM workflow_steps WHERE run_id=? ORDER BY step_index",
        (run_id,),
    ).fetchall()


def get_workflow_events(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM workflow_events WHERE run_id=? ORDER BY created_at",
        (run_id,),
    ).fetchall()


def get_latest_run(
    conn: sqlite3.Connection,
    org_id: str,
    workflow_name: str,
    status: str | None = None,
) -> sqlite3.Row | None:
    if status:
        return conn.execute(
            """
            SELECT * FROM workflow_runs
            WHERE org_id=? AND workflow_name=? AND status=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (org_id, workflow_name, status),
        ).fetchone()
    return conn.execute(
        """
        SELECT * FROM workflow_runs
        WHERE org_id=? AND workflow_name=?
        ORDER BY created_at DESC LIMIT 1
        """,
        (org_id, workflow_name),
    ).fetchone()
