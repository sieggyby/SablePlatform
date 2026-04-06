"""Tests for _check_member_decay alert check."""
from __future__ import annotations

import json
import uuid
from unittest.mock import patch

from sable_platform.db.alerts import create_alert, list_alerts
from sable_platform.workflows.alert_checks import _check_member_decay
from sable_platform.workflows.alert_evaluator import evaluate_alerts


def _make_entity(conn, org_id, entity_id=None, display_name="Test"):
    entity_id = entity_id or uuid.uuid4().hex
    conn.execute(
        "INSERT INTO entities (entity_id, org_id, display_name, source, status) VALUES (?, ?, ?, 'auto', 'confirmed')",
        (entity_id, org_id, display_name),
    )
    conn.commit()
    return entity_id


def _add_tag(conn, entity_id, tag):
    conn.execute(
        "INSERT INTO entity_tags (entity_id, tag, source, confidence, is_current) VALUES (?, ?, 'test', 1.0, 1)",
        (entity_id, tag),
    )
    conn.commit()


def _insert_decay_score(conn, org_id, entity_id, score, tier):
    conn.execute(
        "INSERT OR REPLACE INTO entity_decay_scores (org_id, entity_id, decay_score, risk_tier, run_date) VALUES (?, ?, ?, ?, '2026-04-01')",
        (org_id, entity_id, score, tier),
    )
    conn.commit()


# --- Fire cases ---


def test_warning_fires_for_high_score(org_db):
    conn, org_id = org_db
    eid = _make_entity(conn, org_id)
    _insert_decay_score(conn, org_id, eid, 0.65, "high")

    alerts = _check_member_decay(conn, org_id)
    assert len(alerts) == 1

    rows = list_alerts(conn, org_id=org_id, status="new")
    assert any(r["alert_type"] == "member_decay" and r["severity"] == "warning" for r in rows)


def test_critical_fires_for_high_score_with_important_tag(org_db):
    conn, org_id = org_db
    eid = _make_entity(conn, org_id)
    _add_tag(conn, eid, "cultist_candidate")
    _insert_decay_score(conn, org_id, eid, 0.85, "critical")

    alerts = _check_member_decay(conn, org_id)
    assert len(alerts) == 1

    rows = list_alerts(conn, org_id=org_id, status="new")
    assert any(r["alert_type"] == "member_decay" and r["severity"] == "critical" for r in rows)


def test_high_score_without_tag_stays_warning(org_db):
    conn, org_id = org_db
    eid = _make_entity(conn, org_id)
    # No tag added — score is high but no structural importance
    _insert_decay_score(conn, org_id, eid, 0.85, "critical")

    alerts = _check_member_decay(conn, org_id)
    assert len(alerts) == 1

    rows = list_alerts(conn, org_id=org_id, status="new")
    assert all(r["severity"] == "warning" for r in rows if r["alert_type"] == "member_decay")


# --- No-fire cases ---


def test_below_threshold_no_alert(org_db):
    conn, org_id = org_db
    eid = _make_entity(conn, org_id)
    _insert_decay_score(conn, org_id, eid, 0.4, "medium")

    alerts = _check_member_decay(conn, org_id)
    assert len(alerts) == 0


def test_no_decay_scores_no_alert(org_db):
    conn, org_id = org_db
    alerts = _check_member_decay(conn, org_id)
    assert len(alerts) == 0


# --- Cooldown suppression ---


def test_cooldown_suppresses_duplicate(org_db):
    conn, org_id = org_db
    eid = _make_entity(conn, org_id)
    _insert_decay_score(conn, org_id, eid, 0.7, "high")

    alerts1 = _check_member_decay(conn, org_id)
    assert len(alerts1) == 1

    # Second evaluation — same dedup_key, alert still 'new'
    alerts2 = _check_member_decay(conn, org_id)
    assert len(alerts2) == 0  # blocked by dedup


# --- Config override ---


def test_config_override_threshold(org_db):
    conn, org_id = org_db
    eid = _make_entity(conn, org_id)
    _insert_decay_score(conn, org_id, eid, 0.5, "medium")

    # Default threshold is 0.6 — score 0.5 would not fire
    alerts = _check_member_decay(conn, org_id)
    assert len(alerts) == 0

    # Lower the threshold via config_json
    conn.execute(
        "UPDATE orgs SET config_json=? WHERE org_id=?",
        (json.dumps({"decay_warning_threshold": 0.4}), org_id),
    )
    conn.commit()

    alerts = _check_member_decay(conn, org_id)
    assert len(alerts) == 1


# --- Integration with evaluate_alerts ---


def test_member_decay_in_evaluate_alerts(org_db):
    conn, org_id = org_db
    eid = _make_entity(conn, org_id)
    _insert_decay_score(conn, org_id, eid, 0.7, "high")

    alerts = evaluate_alerts(conn, org_id)
    member_decay_alerts = [
        a for a in list_alerts(conn, org_id=org_id, status="new")
        if a["alert_type"] == "member_decay"
    ]
    assert len(member_decay_alerts) >= 1


# --- QA-requested: cross-org dedup isolation ---


def test_member_decay_dedup_does_not_cross_orgs(in_memory_db):
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('org_a', 'A', 'active')")
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('org_b', 'B', 'active')")
    conn.commit()

    # Same raw handle "alice" in both orgs (unresolved — stored as-is)
    _insert_decay_score(conn, "org_a", "alice", 0.7, "high")
    _insert_decay_score(conn, "org_b", "alice", 0.7, "high")

    alerts_a = _check_member_decay(conn, "org_a")
    alerts_b = _check_member_decay(conn, "org_b")
    assert len(alerts_a) == 1
    assert len(alerts_b) == 1  # must not be blocked by org_a's alert


# --- QA-requested: config override critical threshold ---


def test_config_override_critical_threshold(org_db):
    conn, org_id = org_db
    eid = _make_entity(conn, org_id)
    _add_tag(conn, eid, "voice")
    _insert_decay_score(conn, org_id, eid, 0.65, "high")

    # Default critical threshold is 0.8 — 0.65 fires as warning (below critical)
    alerts = _check_member_decay(conn, org_id)
    assert len(alerts) == 1
    row = list_alerts(conn, org_id=org_id, status="new")
    assert row[0]["severity"] == "warning"

    # Resolve the alert so we can re-fire
    from sable_platform.db.alerts import resolve_alert
    resolve_alert(conn, alerts[0])

    # Lower critical threshold to 0.6 — now 0.65 with important tag fires as critical
    conn.execute(
        "UPDATE orgs SET config_json=? WHERE org_id=?",
        (json.dumps({"decay_critical_threshold": 0.6}), org_id),
    )
    conn.commit()

    alerts2 = _check_member_decay(conn, org_id)
    assert len(alerts2) == 1
    row2 = list_alerts(conn, org_id=org_id, status="new")
    assert any(r["severity"] == "critical" for r in row2 if r["alert_type"] == "member_decay")
