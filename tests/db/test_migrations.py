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
    # Migration 010: discord pulse
    "discord_pulse_runs",
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
    assert row["version"] == 13


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
    assert row["version"] == 13


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


def test_alert_cooldown_columns():
    """Migration 011: alert_configs.cooldown_hours and alerts.last_delivered_at exist."""
    conn = _make_conn()
    ensure_schema(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(alert_configs)").fetchall()}
    assert "cooldown_hours" in cols, "alert_configs missing 'cooldown_hours'"

    cols = {row[1] for row in conn.execute("PRAGMA table_info(alerts)").fetchall()}
    assert "last_delivered_at" in cols, "alerts missing 'last_delivered_at'"


def test_workflow_step_fingerprint_column():
    """Migration 012: workflow_runs.step_fingerprint exists."""
    conn = _make_conn()
    ensure_schema(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(workflow_runs)").fetchall()}
    assert "step_fingerprint" in cols, "workflow_runs missing 'step_fingerprint'"


def test_cooldown_hours_default_is_4():
    """Migration 011: alert_configs rows inserted without cooldown_hours must default to 4."""
    conn = _make_conn()
    ensure_schema(conn)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('x', 'X', 'active')")
    config_id = "cfg_default_test"
    conn.execute(
        "INSERT INTO alert_configs (config_id, org_id, min_severity, enabled) VALUES (?, 'x', 'warning', 1)",
        (config_id,),
    )
    conn.commit()
    row = conn.execute("SELECT cooldown_hours FROM alert_configs WHERE config_id=?", (config_id,)).fetchone()
    assert row["cooldown_hours"] == 4, f"expected cooldown_hours=4, got {row['cooldown_hours']}"


def test_last_delivered_at_is_null_on_new_alert():
    """Migration 011: freshly created alerts must have last_delivered_at=NULL."""
    conn = _make_conn()
    ensure_schema(conn)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('y', 'Y', 'active')")
    alert_id = "alert_null_test"
    conn.execute(
        "INSERT INTO alerts (alert_id, org_id, alert_type, severity, title, status, dedup_key) "
        "VALUES (?, 'y', 'tracking_stale', 'critical', 'T', 'new', 'dk1')",
        (alert_id,),
    )
    conn.commit()
    row = conn.execute("SELECT last_delivered_at FROM alerts WHERE alert_id=?", (alert_id,)).fetchone()
    assert row["last_delivered_at"] is None, "last_delivered_at must be NULL on new alerts"


def test_alert_delivery_error_column():
    """Migration 013: alerts.last_delivery_error exists and is NULL by default."""
    conn = _make_conn()
    ensure_schema(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(alerts)").fetchall()}
    assert "last_delivery_error" in cols, "alerts missing 'last_delivery_error'"

    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('de', 'DE', 'active')")
    alert_id = "de_alert_test"
    conn.execute(
        "INSERT INTO alerts (alert_id, org_id, alert_type, severity, title, status, dedup_key) "
        "VALUES (?, 'de', 'tracking_stale', 'critical', 'T', 'new', 'de_dk')",
        (alert_id,),
    )
    conn.commit()
    row = conn.execute("SELECT last_delivery_error FROM alerts WHERE alert_id=?", (alert_id,)).fetchone()
    assert row["last_delivery_error"] is None, "last_delivery_error must be NULL by default"


def test_step_fingerprint_is_null_on_new_run():
    """Migration 012: workflow_runs inserted without step_fingerprint must have NULL."""
    conn = _make_conn()
    ensure_schema(conn)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('z', 'Z', 'active')")
    run_id = "run_null_test"
    conn.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, config_json) "
        "VALUES (?, 'z', 'test_wf', 'completed', '{}')",
        (run_id,),
    )
    conn.commit()
    row = conn.execute("SELECT step_fingerprint FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
    assert row["step_fingerprint"] is None, "step_fingerprint must be NULL when not set (legacy run)"


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
