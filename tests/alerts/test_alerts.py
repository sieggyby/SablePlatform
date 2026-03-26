"""Tests for Proactive Alerting (Feature 4)."""
from __future__ import annotations

import datetime
import uuid

import pytest

from sable_platform.db.alerts import (
    create_alert,
    acknowledge_alert,
    resolve_alert,
    list_alerts,
    upsert_alert_config,
    get_alert_config,
)
from sable_platform.workflows.alert_evaluator import evaluate_alerts
from sable_platform.workflows.engine import WorkflowRunner
from sable_platform.workflows.builtins.alert_check import ALERT_CHECK


def _ts(days_ago: int) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _make_entity(conn, org_id):
    entity_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO entities (entity_id, org_id, display_name, source, status) VALUES (?, ?, 'X', 'cult_doctor', 'provisional')",
        (entity_id, org_id),
    )
    conn.commit()
    return entity_id


# ---------------------------------------------------------------------------
# Alert deduplication
# ---------------------------------------------------------------------------

def test_dedup_blocks_duplicate_new_alert(org_db):
    conn, org_id = org_db
    aid1 = create_alert(conn, "test_type", "info", "First alert",
                        org_id=org_id, dedup_key="test:key1")
    aid2 = create_alert(conn, "test_type", "info", "Duplicate",
                        org_id=org_id, dedup_key="test:key1")
    assert aid1 is not None
    assert aid2 is None  # blocked by dedup


def test_dedup_allows_after_resolve(org_db):
    conn, org_id = org_db
    aid1 = create_alert(conn, "test_type", "info", "First",
                        org_id=org_id, dedup_key="test:key2")
    assert aid1 is not None
    resolve_alert(conn, aid1)

    aid2 = create_alert(conn, "test_type", "info", "After resolve",
                        org_id=org_id, dedup_key="test:key2")
    assert aid2 is not None  # allowed — previous was resolved


def test_dedup_allows_acknowledged_alert(org_db):
    """Acknowledged alerts allow re-alerting — dedup only blocks status='new'."""
    conn, org_id = org_db
    aid1 = create_alert(conn, "test_type", "info", "First",
                        org_id=org_id, dedup_key="test:ack_key")
    assert aid1 is not None
    acknowledge_alert(conn, aid1, "operator_bob")

    aid2 = create_alert(conn, "test_type", "info", "After ack",
                        org_id=org_id, dedup_key="test:ack_key")
    assert aid2 is not None  # acknowledged ≠ new — re-alerting allowed


def test_dedup_allows_no_key(org_db):
    """Alerts with no dedup_key are never blocked."""
    conn, org_id = org_db
    a1 = create_alert(conn, "t", "info", "No dedup 1", org_id=org_id)
    a2 = create_alert(conn, "t", "info", "No dedup 2", org_id=org_id)
    assert a1 is not None
    assert a2 is not None


# ---------------------------------------------------------------------------
# Alert lifecycle
# ---------------------------------------------------------------------------

def test_acknowledge_alert(org_db):
    conn, org_id = org_db
    aid = create_alert(conn, "test", "warning", "Alert", org_id=org_id)
    acknowledge_alert(conn, aid, "operator_alice")
    row = conn.execute("SELECT * FROM alerts WHERE alert_id=?", (aid,)).fetchone()
    assert row["status"] == "acknowledged"
    assert row["acknowledged_by"] == "operator_alice"
    assert row["acknowledged_at"] is not None


def test_list_alerts_filter_severity(org_db):
    conn, org_id = org_db
    create_alert(conn, "t1", "critical", "Crit", org_id=org_id)
    create_alert(conn, "t2", "info", "Info", org_id=org_id)
    crits = list_alerts(conn, org_id=org_id, severity="critical")
    assert len(crits) == 1
    assert crits[0]["severity"] == "critical"


# ---------------------------------------------------------------------------
# Alert evaluator: workflow_failed
# ---------------------------------------------------------------------------

def test_evaluate_failed_workflow_creates_critical(org_db):
    conn, org_id = org_db
    # Insert a failed workflow run
    run_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, error)
        VALUES (?, ?, 'weekly_client_loop', 'failed', 'step failed')
        """,
        (run_id, org_id),
    )
    conn.commit()

    alert_ids = evaluate_alerts(conn, org_id=org_id)
    assert len(alert_ids) >= 1
    alert_rows = list_alerts(conn, org_id=org_id, severity="critical")
    assert any(r["alert_type"] == "workflow_failed" for r in alert_rows)


def test_evaluate_no_failed_workflow_no_alert(org_db):
    conn, org_id = org_db
    run_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status) VALUES (?, ?, 'weekly_client_loop', 'completed')",
        (run_id, org_id),
    )
    conn.commit()

    evaluate_alerts(conn, org_id=org_id)
    workflow_alerts = [r for r in list_alerts(conn, org_id=org_id, status="new")
                       if r["alert_type"] == "workflow_failed"]
    assert len(workflow_alerts) == 0


# ---------------------------------------------------------------------------
# Alert evaluator: tracking_stale
# ---------------------------------------------------------------------------

def test_evaluate_stale_tracking_creates_critical(org_db):
    conn, org_id = org_db
    # Insert sync that is 18 days old
    conn.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status, completed_at) VALUES (?, 'sable_tracking', 'completed', ?)",
        (org_id, _ts(18)),
    )
    conn.commit()

    evaluate_alerts(conn, org_id=org_id)
    rows = list_alerts(conn, org_id=org_id, severity="critical")
    assert any(r["alert_type"] == "tracking_stale" for r in rows)


def test_evaluate_fresh_tracking_no_stale_alert(org_db):
    conn, org_id = org_db
    # Insert sync that is 3 days old — not stale
    conn.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status, completed_at) VALUES (?, 'sable_tracking', 'completed', ?)",
        (org_id, _ts(3)),
    )
    conn.commit()

    evaluate_alerts(conn, org_id=org_id)
    rows = list_alerts(conn, org_id=org_id, severity="critical")
    assert not any(r["alert_type"] == "tracking_stale" for r in rows)


def test_evaluate_no_tracking_data_creates_critical(org_db):
    conn, org_id = org_db
    # No sync_runs at all
    evaluate_alerts(conn, org_id=org_id)
    rows = list_alerts(conn, org_id=org_id, severity="critical")
    assert any(r["alert_type"] == "tracking_stale" for r in rows)


# ---------------------------------------------------------------------------
# Alert evaluator: sentiment_shift
# ---------------------------------------------------------------------------

def test_evaluate_sentiment_shift_creates_warning(org_db):
    conn, org_id = org_db
    run_before = conn.execute(
        "INSERT INTO diagnostic_runs (org_id, project_slug, run_type, run_date, status) VALUES (?, 'p', 'full', '2026-01-01', 'completed')",
        (org_id,),
    ).lastrowid
    run_after = conn.execute(
        "INSERT INTO diagnostic_runs (org_id, project_slug, run_type, run_date, status) VALUES (?, 'p', 'full', '2026-02-01', 'completed')",
        (org_id,),
    ).lastrowid
    conn.commit()
    # Insert a delta showing sentiment spike
    conn.execute(
        """
        INSERT INTO diagnostic_deltas
            (delta_id, org_id, run_id_before, run_id_after, metric_name,
             value_before, value_after, delta)
        VALUES (?, ?, ?, ?, 'sentiment_negative', 0.08, 0.25, 0.17)
        """,
        (uuid.uuid4().hex, org_id, run_before, run_after),
    )
    conn.commit()

    evaluate_alerts(conn, org_id=org_id)
    rows = list_alerts(conn, org_id=org_id, severity="warning")
    assert any(r["alert_type"] == "sentiment_shift" for r in rows)


# ---------------------------------------------------------------------------
# Dedup prevents double-firing
# ---------------------------------------------------------------------------

def test_evaluate_no_double_fire(org_db):
    conn, org_id = org_db
    # 18-day stale tracking
    conn.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status, completed_at) VALUES (?, 'sable_tracking', 'completed', ?)",
        (org_id, _ts(18)),
    )
    conn.commit()

    ids_first = evaluate_alerts(conn, org_id=org_id)
    ids_second = evaluate_alerts(conn, org_id=org_id)

    all_alerts = conn.execute(
        "SELECT alert_id, alert_type FROM alerts WHERE alert_type='tracking_stale' AND org_id=?",
        (org_id,),
    ).fetchall()
    assert len(all_alerts) == 1  # Only one tracking_stale alert created across both evaluations


# ---------------------------------------------------------------------------
# Alert config
# ---------------------------------------------------------------------------

def test_upsert_alert_config(org_db):
    conn, org_id = org_db
    config_id = upsert_alert_config(conn, org_id, min_severity="critical")
    cfg = get_alert_config(conn, org_id)
    assert cfg is not None
    assert cfg["min_severity"] == "critical"
    assert cfg["enabled"] == 1

    # Update
    upsert_alert_config(conn, org_id, min_severity="info", enabled=False)
    cfg2 = get_alert_config(conn, org_id)
    assert cfg2["min_severity"] == "info"
    assert cfg2["enabled"] == 0


# ---------------------------------------------------------------------------
# alert_check workflow
# ---------------------------------------------------------------------------

def test_alert_check_workflow_completes(org_db):
    conn, org_id = org_db
    runner = WorkflowRunner(ALERT_CHECK)
    run_id = runner.run(org_id, {"org_id": org_id}, conn=conn)
    from sable_platform.db.workflow_store import get_workflow_run
    run = get_workflow_run(conn, run_id)
    assert run["status"] == "completed"
