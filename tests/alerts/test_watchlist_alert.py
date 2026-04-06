"""Tests for _check_watchlist_changes alert check."""
from __future__ import annotations

from sable_platform.db.alerts import list_alerts
from sable_platform.db.watchlist import add_to_watchlist, take_all_snapshots
from sable_platform.workflows.alert_checks import _check_watchlist_changes
from sable_platform.workflows.alert_evaluator import evaluate_alerts


def test_watchlist_change_fires_warning(org_db):
    conn, org_id = org_db
    add_to_watchlist(conn, org_id, "alice", "op")

    # Simulate a decay score change
    conn.execute(
        "INSERT INTO entity_decay_scores (org_id, entity_id, decay_score, risk_tier, run_date) "
        "VALUES (?, ?, 0.5, 'medium', '2026-04-01')",
        (org_id, "alice"),
    )
    conn.commit()

    alerts = _check_watchlist_changes(conn, org_id)
    assert len(alerts) == 1

    rows = list_alerts(conn, org_id=org_id, status="new")
    assert any(r["alert_type"] == "watchlist_change" and r["severity"] == "warning" for r in rows)


def test_large_decay_shift_fires_critical(org_db):
    conn, org_id = org_db

    # Insert initial decay score before watching
    conn.execute(
        "INSERT INTO entity_decay_scores (org_id, entity_id, decay_score, risk_tier, run_date) "
        "VALUES (?, ?, 0.3, 'low', '2026-04-01')",
        (org_id, "alice"),
    )
    conn.commit()
    add_to_watchlist(conn, org_id, "alice", "op")

    # Increase decay by >= 0.1
    conn.execute(
        "UPDATE entity_decay_scores SET decay_score=0.5 WHERE org_id=? AND entity_id='alice'",
        (org_id,),
    )
    conn.commit()

    alerts = _check_watchlist_changes(conn, org_id)
    assert len(alerts) == 1

    rows = list_alerts(conn, org_id=org_id, status="new")
    assert any(r["alert_type"] == "watchlist_change" and r["severity"] == "critical" for r in rows)


def test_no_changes_no_alert(org_db):
    conn, org_id = org_db
    add_to_watchlist(conn, org_id, "alice", "op")

    # Take another snapshot — nothing changed
    take_all_snapshots(conn, org_id)

    alerts = _check_watchlist_changes(conn, org_id)
    assert len(alerts) == 0


def test_cooldown_suppresses_duplicate(org_db):
    conn, org_id = org_db
    add_to_watchlist(conn, org_id, "alice", "op")

    conn.execute(
        "INSERT INTO entity_decay_scores (org_id, entity_id, decay_score, risk_tier, run_date) "
        "VALUES (?, ?, 0.5, 'medium', '2026-04-01')",
        (org_id, "alice"),
    )
    conn.commit()

    alerts1 = _check_watchlist_changes(conn, org_id)
    assert len(alerts1) == 1

    # Bump decay again
    conn.execute(
        "UPDATE entity_decay_scores SET decay_score=0.6 WHERE org_id=? AND entity_id='alice'",
        (org_id,),
    )
    conn.commit()

    alerts2 = _check_watchlist_changes(conn, org_id)
    assert len(alerts2) == 0  # dedup blocks


def test_watchlist_in_evaluate_alerts(org_db):
    conn, org_id = org_db
    add_to_watchlist(conn, org_id, "alice", "op")

    conn.execute(
        "INSERT INTO entity_decay_scores (org_id, entity_id, decay_score, risk_tier, run_date) "
        "VALUES (?, ?, 0.5, 'medium', '2026-04-01')",
        (org_id, "alice"),
    )
    conn.commit()

    evaluate_alerts(conn, org_id)
    rows = list_alerts(conn, org_id=org_id, status="new")
    assert any(r["alert_type"] == "watchlist_change" for r in rows)
