"""Tests for _check_bridge_decay alert check."""
from __future__ import annotations

import json

from sable_platform.db.alerts import list_alerts
from sable_platform.workflows.alert_checks import _check_bridge_decay
from sable_platform.workflows.alert_evaluator import evaluate_alerts


def _insert_centrality(conn, org_id, entity_id, degree, in_cent=0.3, out_cent=0.2):
    conn.execute(
        "INSERT OR REPLACE INTO entity_centrality_scores "
        "(org_id, entity_id, degree_centrality, in_centrality, out_centrality, run_date) "
        "VALUES (?, ?, ?, ?, ?, '2026-04-01')",
        (org_id, entity_id, degree, in_cent, out_cent),
    )
    conn.commit()


def _insert_decay(conn, org_id, entity_id, score, tier="high"):
    conn.execute(
        "INSERT OR REPLACE INTO entity_decay_scores "
        "(org_id, entity_id, decay_score, risk_tier, run_date) "
        "VALUES (?, ?, ?, ?, '2026-04-01')",
        (org_id, entity_id, score, tier),
    )
    conn.commit()


def test_bridge_decay_fires_critical(org_db):
    conn, org_id = org_db
    _insert_centrality(conn, org_id, "alice", degree=0.5)
    _insert_decay(conn, org_id, "alice", 0.7)

    alerts = _check_bridge_decay(conn, org_id)
    assert len(alerts) == 1

    rows = list_alerts(conn, org_id=org_id, status="new")
    assert any(r["alert_type"] == "bridge_decay" and r["severity"] == "critical" for r in rows)


def test_low_centrality_no_alert(org_db):
    conn, org_id = org_db
    _insert_centrality(conn, org_id, "alice", degree=0.1)
    _insert_decay(conn, org_id, "alice", 0.8)

    alerts = _check_bridge_decay(conn, org_id)
    assert len(alerts) == 0


def test_low_decay_no_alert(org_db):
    conn, org_id = org_db
    _insert_centrality(conn, org_id, "alice", degree=0.5)
    _insert_decay(conn, org_id, "alice", 0.3)

    alerts = _check_bridge_decay(conn, org_id)
    assert len(alerts) == 0


def test_cooldown_suppresses_duplicate(org_db):
    conn, org_id = org_db
    _insert_centrality(conn, org_id, "alice", degree=0.5)
    _insert_decay(conn, org_id, "alice", 0.7)

    alerts1 = _check_bridge_decay(conn, org_id)
    assert len(alerts1) == 1

    alerts2 = _check_bridge_decay(conn, org_id)
    assert len(alerts2) == 0


def test_config_override_thresholds(org_db):
    conn, org_id = org_db
    _insert_centrality(conn, org_id, "alice", degree=0.2)
    _insert_decay(conn, org_id, "alice", 0.5)

    # Default thresholds: centrality 0.3, decay 0.6 — should not fire
    alerts = _check_bridge_decay(conn, org_id)
    assert len(alerts) == 0

    # Lower thresholds via config
    conn.execute(
        "UPDATE orgs SET config_json=? WHERE org_id=?",
        (json.dumps({"bridge_centrality_threshold": 0.1, "bridge_decay_threshold": 0.4}), org_id),
    )
    conn.commit()

    alerts = _check_bridge_decay(conn, org_id)
    assert len(alerts) == 1


def test_bridge_decay_in_evaluate_alerts(org_db):
    conn, org_id = org_db
    _insert_centrality(conn, org_id, "alice", degree=0.5)
    _insert_decay(conn, org_id, "alice", 0.7)

    evaluate_alerts(conn, org_id)
    rows = list_alerts(conn, org_id=org_id, status="new")
    assert any(r["alert_type"] == "bridge_decay" for r in rows)
