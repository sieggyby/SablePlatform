"""Migration and schema tests."""
from __future__ import annotations

import sqlite3

from sable_platform.db.connection import ensure_schema


EXPECTED_TABLES = {
    # Original 15 tables
    "schema_version", "orgs", "entities", "entity_handles", "entity_tags",
    "entity_notes", "merge_candidates", "merge_events", "content_items",
    "diagnostic_runs", "jobs", "job_steps", "artifacts", "cost_events", "sync_runs",
    # Migration 006: 3 new tables
    "workflow_runs", "workflow_steps", "workflow_events",
    # Migration 007: actions + outcomes + diagnostic_deltas
    "actions", "outcomes", "diagnostic_deltas",
    # Migration 008: entity journey
    "entity_tag_history",
    # Migration 009: alerts
    "alerts", "alert_configs",
}


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def test_fresh_db_reaches_current_version():
    conn = _make_conn()
    ensure_schema(conn)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row["version"] == 10


def test_all_tables_exist():
    conn = _make_conn()
    ensure_schema(conn)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    for expected in EXPECTED_TABLES:
        assert expected in tables, f"Table '{expected}' not found"


def test_idempotent_schema():
    conn = _make_conn()
    ensure_schema(conn)
    ensure_schema(conn)  # Run again — should not raise
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row["version"] == 10


def test_workflow_tables_columns():
    conn = _make_conn()
    ensure_schema(conn)

    # workflow_runs must have these columns
    cols = {row[1] for row in conn.execute("PRAGMA table_info(workflow_runs)").fetchall()}
    for expected in ("run_id", "org_id", "workflow_name", "status", "config_json", "started_at", "completed_at", "error"):
        assert expected in cols, f"workflow_runs missing column '{expected}'"

    # workflow_steps must have these columns
    cols = {row[1] for row in conn.execute("PRAGMA table_info(workflow_steps)").fetchall()}
    for expected in ("step_id", "run_id", "step_name", "step_index", "status", "retries", "input_json", "output_json", "error"):
        assert expected in cols, f"workflow_steps missing column '{expected}'"


def test_foreign_keys_enabled():
    conn = _make_conn()
    ensure_schema(conn)
    # FK enforcement: inserting workflow_run for nonexistent org should fail
    import pytest
    conn.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO workflow_runs (run_id, org_id, workflow_name) VALUES ('r1', 'noexist', 'test')"
        )
        conn.commit()
