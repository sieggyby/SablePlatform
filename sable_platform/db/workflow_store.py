"""DB persistence for workflow_runs, workflow_steps, and workflow_events."""
from __future__ import annotations

import json
import sqlite3
import uuid


def create_workflow_run(
    conn: sqlite3.Connection,
    org_id: str,
    workflow_name: str,
    workflow_version: str,
    config: dict,
) -> str:
    run_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO workflow_runs (run_id, org_id, workflow_name, workflow_version, status, config_json)
        VALUES (?, ?, ?, ?, 'pending', ?)
        """,
        (run_id, org_id, workflow_name, workflow_version, json.dumps(config)),
    )
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
        (error, run_id),
    )
    conn.commit()


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
        (json.dumps({"reason": reason}), step_id),
    )
    conn.commit()


def fail_workflow_step(conn: sqlite3.Connection, step_id: str, error: str) -> None:
    conn.execute(
        """
        UPDATE workflow_steps
        SET status='failed', completed_at=datetime('now'), retries=retries+1, error=?
        WHERE step_id=?
        """,
        (error, step_id),
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
