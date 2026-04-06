"""Tests for T3-SELFMON: alert evaluator heartbeat and health staleness reporting."""
from __future__ import annotations

import sqlite3

from sable_platform.db.connection import ensure_schema
from sable_platform.db.health import check_db_health
from sable_platform.workflows.alert_evaluator import evaluate_alerts


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def test_heartbeat_written_after_evaluate_alerts():
    """evaluate_alerts() must write 'last_alert_eval_at' to platform_meta."""
    conn = _make_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('hb_org', 'HB', 'active')")
    conn.commit()

    evaluate_alerts(conn, org_id="hb_org")

    row = conn.execute(
        "SELECT value FROM platform_meta WHERE key='last_alert_eval_at'"
    ).fetchone()
    assert row is not None, "platform_meta must have 'last_alert_eval_at' after evaluate_alerts()"
    assert row["value"]  # non-empty datetime string


def test_heartbeat_upserted_on_repeated_calls():
    """Repeated calls to evaluate_alerts() must update the existing row, not create duplicates."""
    conn = _make_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('hb2', 'HB2', 'active')")
    conn.commit()

    evaluate_alerts(conn, org_id="hb2")
    evaluate_alerts(conn, org_id="hb2")

    count = conn.execute(
        "SELECT COUNT(*) FROM platform_meta WHERE key='last_alert_eval_at'"
    ).fetchone()[0]
    assert count == 1, "platform_meta must have exactly 1 row for 'last_alert_eval_at'"


def test_health_reports_eval_fresh():
    """check_db_health() must report alert_eval_stale=False when heartbeat is recent."""
    conn = _make_conn()
    # Insert a heartbeat 1 hour ago.
    conn.execute(
        "INSERT INTO platform_meta (key, value, updated_at)"
        " VALUES ('last_alert_eval_at', datetime('now', '-1 hours'), datetime('now'))"
    )
    conn.commit()

    health = check_db_health(conn)
    assert health["alert_eval_stale"] is False
    assert health["last_alert_eval_age_hours"] is not None
    assert health["last_alert_eval_age_hours"] < 2.0


def test_health_reports_eval_stale():
    """check_db_health() must report alert_eval_stale=True when heartbeat is >26h old."""
    conn = _make_conn()
    conn.execute(
        "INSERT INTO platform_meta (key, value, updated_at)"
        " VALUES ('last_alert_eval_at', datetime('now', '-30 hours'), datetime('now'))"
    )
    conn.commit()

    health = check_db_health(conn)
    assert health["alert_eval_stale"] is True
    assert health["last_alert_eval_age_hours"] > 26.0


def test_health_no_eval_yet():
    """check_db_health() must report alert_eval_stale=True and age=None when never run."""
    conn = _make_conn()
    health = check_db_health(conn)
    assert health["last_alert_eval_age_hours"] is None
    assert health["alert_eval_stale"] is True
