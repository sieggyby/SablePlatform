"""Architecture boundary tests for the alert_evaluator split."""
from __future__ import annotations

import sqlite3

import sable_platform.workflows.alert_checks as alert_checks
import sable_platform.workflows.alert_delivery as alert_delivery
import sable_platform.workflows.alert_evaluator as alert_evaluator
from sable_platform.db.connection import ensure_schema


_ALL_CHECK_NAMES = [
    "_check_tracking_stale",
    "_check_cultist_tag_expiring",
    "_check_sentiment_shift",
    "_check_mvl_score_change",
    "_check_actions_unclaimed",
    "_check_discord_pulse_stale",
    "_check_stuck_runs",
    "_check_workflow_failures",
    "_check_discord_pulse_regression",
]


def test_module_imports_succeed():
    assert callable(alert_evaluator.evaluate_alerts)
    assert callable(alert_checks._check_tracking_stale)
    assert callable(alert_delivery._deliver)
    assert callable(alert_delivery.deliver_alerts_by_ids)


def test_all_check_functions_in_checks_module():
    for name in _ALL_CHECK_NAMES:
        assert hasattr(alert_checks, name), f"alert_checks missing {name}"


def test_deliver_not_in_evaluator_module():
    assert not hasattr(alert_evaluator, "_deliver"), (
        "_deliver must live in alert_delivery, not alert_evaluator"
    )


def test_checks_do_not_import_deliver():
    """Check functions must not import _deliver — delivery is caller's responsibility."""
    import inspect
    source = inspect.getsource(alert_checks)
    assert "_deliver" not in source, "alert_checks must not reference _deliver"


def test_deliver_alerts_by_ids_skips_missing():
    """deliver_alerts_by_ids silently skips nonexistent alert IDs."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)

    # Should not raise
    alert_delivery.deliver_alerts_by_ids(conn, ["nonexistent_id"])


def test_deliver_alerts_by_ids_calls_deliver():
    """deliver_alerts_by_ids reads alert rows and dispatches via _deliver."""
    from unittest.mock import patch

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)

    conn.execute(
        "INSERT INTO orgs (org_id, display_name, status) VALUES ('dtest', 'D', 'active')"
    )
    conn.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status, completed_at) "
        "VALUES ('dtest', 'sable_tracking', 'completed', datetime('now', '-60 days'))"
    )
    conn.commit()

    alert_ids = alert_evaluator.evaluate_alerts(conn, org_id="dtest")
    assert len(alert_ids) > 0

    with patch.object(alert_delivery, "_deliver") as mock_deliver:
        alert_delivery.deliver_alerts_by_ids(conn, alert_ids)
        assert mock_deliver.call_count == len(alert_ids)


def test_evaluate_alerts_end_to_end():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)

    conn.execute(
        "INSERT INTO orgs (org_id, display_name, status) VALUES ('e2e_org', 'E2E', 'active')"
    )
    # Insert a stale tracking sync (completed 60 days ago)
    conn.execute(
        """
        INSERT INTO sync_runs (org_id, sync_type, status, completed_at)
        VALUES ('e2e_org', 'sable_tracking', 'completed', datetime('now', '-60 days'))
        """
    )
    conn.commit()

    result = alert_evaluator.evaluate_alerts(conn, org_id="e2e_org")
    assert isinstance(result, list)
    assert len(result) > 0


def test_per_org_failure_isolation(monkeypatch):
    """A crash in one org's checks must not prevent other orgs from being evaluated."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)

    for org_id in ("bad_org", "good_org"):
        conn.execute(
            "INSERT INTO orgs (org_id, display_name, status) VALUES (?, ?, 'active')",
            (org_id, org_id),
        )
    # Give good_org a stale sync so it generates an alert
    conn.execute(
        """
        INSERT INTO sync_runs (org_id, sync_type, status, completed_at)
        VALUES ('good_org', 'sable_tracking', 'completed', datetime('now', '-60 days'))
        """
    )
    conn.commit()

    # Patch _check_tracking_stale to raise for bad_org only
    original = alert_checks._check_tracking_stale
    def patched(c, oid):
        if oid == "bad_org":
            raise RuntimeError("simulated DB failure")
        return original(c, oid)
    monkeypatch.setattr(alert_checks, "_check_tracking_stale", patched)

    result = alert_evaluator.evaluate_alerts(conn)
    # good_org's stale alert must still be created despite bad_org crashing
    assert any(True for _ in result), "expected at least one alert from good_org"
    assert len(result) > 0
