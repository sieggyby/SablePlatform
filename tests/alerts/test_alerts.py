"""Tests for Proactive Alerting (Feature 4)."""
from __future__ import annotations

import datetime
import uuid
from unittest.mock import patch, MagicMock

import pytest

from sable_platform.db.alerts import (
    create_alert,
    acknowledge_alert,
    resolve_alert,
    list_alerts,
    upsert_alert_config,
    get_alert_config,
    get_last_delivered_at,
    mark_delivered,
    mark_delivery_failed,
)
from sable_platform.workflows.alert_evaluator import evaluate_alerts
from sable_platform.workflows.alert_checks import _check_discord_pulse_regression
from sable_platform.workflows.alert_delivery import _deliver, _send_telegram, _send_discord
from sable_platform.workflows.engine import WorkflowRunner
from sable_platform.workflows.builtins.alert_check import ALERT_CHECK


def _ts(days_ago: int) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _make_entity(conn, org_id):
    entity_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO entities (entity_id, org_id, display_name, source, status) VALUES (?, ?, 'X', 'cult_doctor', 'confirmed')",
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


def test_dedup_blocks_acknowledged_alert(org_db):
    """Acknowledged alerts still block re-alerting — only resolved allows."""
    conn, org_id = org_db
    aid1 = create_alert(conn, "test_type", "info", "First",
                        org_id=org_id, dedup_key="test:ack_key")
    assert aid1 is not None
    acknowledge_alert(conn, aid1, "operator_bob")

    aid2 = create_alert(conn, "test_type", "info", "After ack",
                        org_id=org_id, dedup_key="test:ack_key")
    assert aid2 is None  # acknowledged still blocks — operator already aware


def test_dedup_allows_no_key(org_db):
    """Alerts with no dedup_key are never blocked."""
    conn, org_id = org_db
    a1 = create_alert(conn, "t", "info", "No dedup 1", org_id=org_id)
    a2 = create_alert(conn, "t", "info", "No dedup 2", org_id=org_id)
    assert a1 is not None
    assert a2 is not None


def test_dedup_full_lifecycle(org_db):
    """End-to-end: new blocks → ack blocks → resolve unblocks."""
    conn, org_id = org_db
    key = "lifecycle:test"

    # Create first alert
    aid1 = create_alert(conn, "t", "info", "First", org_id=org_id, dedup_key=key)
    assert aid1 is not None

    # Duplicate blocked while status='new'
    assert create_alert(conn, "t", "info", "Dup", org_id=org_id, dedup_key=key) is None

    # Acknowledge — still blocked
    acknowledge_alert(conn, aid1, "op")
    assert create_alert(conn, "t", "info", "Post-ack", org_id=org_id, dedup_key=key) is None

    # Resolve — now unblocked
    resolve_alert(conn, aid1)
    aid2 = create_alert(conn, "t", "info", "After resolve", org_id=org_id, dedup_key=key)
    assert aid2 is not None
    assert aid2 != aid1


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


# ---------------------------------------------------------------------------
# P2-2: workflow_failures time window — old failures should not re-alert
# ---------------------------------------------------------------------------

def test_old_workflow_failure_does_not_alert(org_db):
    """A workflow_run that failed 31+ days ago must not produce a new alert."""
    conn, org_id = org_db
    old_ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=31)
    ).strftime("%Y-%m-%d %H:%M:%S")
    run_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, error, created_at)
        VALUES (?, ?, 'weekly_client_loop', 'failed', 'old error', ?)
        """,
        (run_id, org_id, old_ts),
    )
    conn.commit()

    evaluate_alerts(conn, org_id=org_id)
    rows = [r for r in list_alerts(conn, org_id=org_id, severity="critical")
            if r["alert_type"] == "workflow_failed"]
    assert len(rows) == 0


def test_recent_workflow_failure_does_alert(org_db):
    """A workflow_run that failed 1 day ago must produce an alert."""
    conn, org_id = org_db
    recent_ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    ).strftime("%Y-%m-%d %H:%M:%S")
    run_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, error, created_at)
        VALUES (?, ?, 'weekly_client_loop', 'failed', 'recent error', ?)
        """,
        (run_id, org_id, recent_ts),
    )
    conn.commit()

    evaluate_alerts(conn, org_id=org_id)
    rows = [r for r in list_alerts(conn, org_id=org_id, severity="critical")
            if r["alert_type"] == "workflow_failed"]
    assert len(rows) >= 1


# ---------------------------------------------------------------------------
# Feature 1: Alert delivery — Telegram + Discord
# ---------------------------------------------------------------------------

def test_telegram_delivery_called_when_configured(org_db):
    """urlopen is called when telegram_chat_id is configured and token present."""
    from sable_platform.workflows.alert_delivery import deliver_alerts_by_ids

    conn, org_id = org_db
    upsert_alert_config(conn, org_id, telegram_chat_id="123456789")

    # Insert a stale sync so alerts are created
    conn.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status, completed_at) VALUES (?, 'sable_tracking', 'completed', ?)",
        (org_id, _ts(18)),
    )
    conn.commit()

    alert_ids = evaluate_alerts(conn, org_id=org_id)
    with patch("sable_platform.workflows.alert_delivery.os.environ.get", return_value="fake-token"):
        with patch("urllib.request.urlopen") as mock_urlopen:
            deliver_alerts_by_ids(conn, alert_ids)
            assert mock_urlopen.called


def test_telegram_delivery_failure_does_not_propagate():
    """urlopen raising must not propagate out of _send_telegram; returns error string."""
    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
        result = _send_telegram("fake-token", "123", "test message")
    assert result is not None
    assert "network error" in result


def test_telegram_delivery_success_returns_none():
    """Successful _send_telegram returns None."""
    mock_response = MagicMock()
    with patch("urllib.request.urlopen", return_value=mock_response):
        result = _send_telegram("fake-token", "123", "test message")
    assert result is None


def test_discord_delivery_failure_does_not_propagate():
    """urlopen raising must not propagate out of _send_discord; returns error string."""
    with patch("urllib.request.urlopen", side_effect=Exception("webhook error")):
        result = _send_discord("https://discord.com/fake-webhook", "test message")
    assert result is not None
    assert "webhook error" in result


def test_discord_delivery_success_returns_none():
    """Successful _send_discord returns None."""
    mock_response = MagicMock()
    with patch("urllib.request.urlopen", return_value=mock_response):
        result = _send_discord("https://discord.com/fake-webhook", "test message")
    assert result is None


# ---------------------------------------------------------------------------
# Discord pulse regression alerts
# ---------------------------------------------------------------------------

def test_discord_pulse_regression_alert_fires(org_db):
    """Alert fires when retention_delta drops more than threshold."""
    conn, org_id = org_db
    conn.execute(
        """
        INSERT INTO discord_pulse_runs
            (org_id, project_slug, run_date, wow_retention_rate, retention_delta)
        VALUES (?, 'multisynq', '2026-03-26', 0.45, -0.07)
        """,
        (org_id,),
    )
    conn.commit()

    alert_ids = evaluate_alerts(conn, org_id=org_id)
    rows = list_alerts(conn, org_id=org_id, severity="warning")
    assert any(r["alert_type"] == "discord_pulse_regression" for r in rows)


def test_discord_pulse_regression_skips_null_delta(org_db):
    """No alert when retention_delta is NULL (first run, no prior week)."""
    conn, org_id = org_db
    conn.execute(
        """
        INSERT INTO discord_pulse_runs
            (org_id, project_slug, run_date, wow_retention_rate, retention_delta)
        VALUES (?, 'multisynq', '2026-03-26', 0.60, NULL)
        """,
        (org_id,),
    )
    conn.commit()

    evaluate_alerts(conn, org_id=org_id)
    rows = [r for r in list_alerts(conn, org_id=org_id, severity="warning")
            if r["alert_type"] == "discord_pulse_regression"]
    assert len(rows) == 0


def test_discord_pulse_regression_skips_positive_delta(org_db):
    """No alert when retention_delta is positive (improvement)."""
    conn, org_id = org_db
    conn.execute(
        """
        INSERT INTO discord_pulse_runs
            (org_id, project_slug, run_date, wow_retention_rate, retention_delta)
        VALUES (?, 'multisynq', '2026-03-26', 0.75, 0.03)
        """,
        (org_id,),
    )
    conn.commit()

    evaluate_alerts(conn, org_id=org_id)
    rows = [r for r in list_alerts(conn, org_id=org_id, severity="warning")
            if r["alert_type"] == "discord_pulse_regression"]
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# Alert cooldown
# ---------------------------------------------------------------------------

def test_cooldown_first_delivery_null(org_db):
    """First delivery (no prior last_delivered_at) proceeds normally."""
    conn, org_id = org_db
    upsert_alert_config(conn, org_id, discord_webhook_url="https://discord.com/fake", min_severity="info")
    create_alert(conn, "test_type", "info", "First", org_id=org_id, dedup_key="cooldown:test1")

    with patch("urllib.request.urlopen") as mock_urlopen:
        _deliver(conn, org_id, "info", "test message", dedup_key="cooldown:test1")
        assert mock_urlopen.called


def test_cooldown_suppresses_within_window(org_db):
    """Delivery is suppressed when last_delivered_at is within the cooldown window."""
    conn, org_id = org_db
    upsert_alert_config(conn, org_id, discord_webhook_url="https://discord.com/fake", min_severity="info")
    create_alert(conn, "test_type", "info", "Alert", org_id=org_id, dedup_key="cooldown:test2")

    # Set last_delivered_at to 1 hour ago (within default 4-hour cooldown)
    recent_ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    ).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE alerts SET last_delivered_at=? WHERE dedup_key='cooldown:test2'",
        (recent_ts,),
    )
    conn.commit()

    with patch("urllib.request.urlopen") as mock_urlopen:
        _deliver(conn, org_id, "info", "should be suppressed", dedup_key="cooldown:test2")
        assert not mock_urlopen.called


def test_cooldown_allows_after_expiry(org_db):
    """Delivery proceeds after cooldown window has expired."""
    conn, org_id = org_db
    upsert_alert_config(conn, org_id, discord_webhook_url="https://discord.com/fake", min_severity="info")
    create_alert(conn, "test_type", "info", "Alert", org_id=org_id, dedup_key="cooldown:test3")

    # Set last_delivered_at to 5 hours ago (beyond default 4-hour cooldown)
    old_ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=5)
    ).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE alerts SET last_delivered_at=? WHERE dedup_key='cooldown:test3'",
        (old_ts,),
    )
    conn.commit()

    with patch("urllib.request.urlopen") as mock_urlopen:
        _deliver(conn, org_id, "info", "should be delivered", dedup_key="cooldown:test3")
        assert mock_urlopen.called


def test_cooldown_zero_disables(org_db):
    """cooldown_hours=0 means cooldown is disabled — always deliver."""
    conn, org_id = org_db
    # Create config with cooldown_hours=0
    conn.execute(
        "INSERT INTO alert_configs (config_id, org_id, min_severity, discord_webhook_url, enabled, cooldown_hours)"
        " VALUES (?, ?, 'info', 'https://discord.com/fake', 1, 0)",
        (uuid.uuid4().hex, org_id),
    )
    conn.commit()
    create_alert(conn, "test_type", "info", "Alert", org_id=org_id, dedup_key="cooldown:test4")

    # Set recent last_delivered_at — should be ignored since cooldown=0
    recent_ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=30)
    ).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE alerts SET last_delivered_at=? WHERE dedup_key='cooldown:test4'",
        (recent_ts,),
    )
    conn.commit()

    with patch("urllib.request.urlopen") as mock_urlopen:
        _deliver(conn, org_id, "info", "no cooldown", dedup_key="cooldown:test4")
        assert mock_urlopen.called


# ---------------------------------------------------------------------------
# Discord pulse stale guard
# ---------------------------------------------------------------------------

def test_discord_pulse_stale_no_rows(org_db):
    """Alert fires when there are no discord_pulse_runs for the org."""
    conn, org_id = org_db
    evaluate_alerts(conn, org_id=org_id)
    rows = [r for r in list_alerts(conn, org_id=org_id, severity="warning")
            if r["alert_type"] == "discord_pulse_stale"]
    assert len(rows) >= 1


def test_discord_pulse_stale_old_data(org_db):
    """Alert fires when the most recent pulse run is older than DISCORD_PULSE_STALE_DAYS."""
    conn, org_id = org_db
    old_date = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)
    ).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO discord_pulse_runs (org_id, project_slug, run_date, wow_retention_rate) VALUES (?, 'proj', ?, 0.5)",
        (org_id, old_date),
    )
    conn.commit()

    evaluate_alerts(conn, org_id=org_id)
    rows = [r for r in list_alerts(conn, org_id=org_id, severity="warning")
            if r["alert_type"] == "discord_pulse_stale"]
    assert len(rows) >= 1


def test_discord_pulse_stale_fresh_no_alert(org_db):
    """No alert when discord_pulse_run data is recent (within DISCORD_PULSE_STALE_DAYS)."""
    conn, org_id = org_db
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO discord_pulse_runs (org_id, project_slug, run_date, wow_retention_rate) VALUES (?, 'proj', ?, 0.6)",
        (org_id, today),
    )
    conn.commit()

    evaluate_alerts(conn, org_id=org_id)
    rows = [r for r in list_alerts(conn, org_id=org_id, severity="warning")
            if r["alert_type"] == "discord_pulse_stale"]
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# Stuck workflow run alert
# ---------------------------------------------------------------------------

def test_stuck_run_fires_warning(org_db):
    """Alert fires for a workflow run stuck in 'running' for >2 hours."""
    conn, org_id = org_db
    run_id = uuid.uuid4().hex
    old_started = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)
    ).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, started_at) VALUES (?, ?, 'test_wf', 'running', ?)",
        (run_id, org_id, old_started),
    )
    conn.commit()

    evaluate_alerts(conn, org_id=org_id)
    rows = [r for r in list_alerts(conn, org_id=org_id, severity="warning")
            if r["alert_type"] == "stuck_run"]
    assert len(rows) >= 1
    assert rows[0]["run_id"] == run_id


def test_recent_run_no_alert(org_db):
    """No stuck_run alert for a run started less than 2 hours ago."""
    conn, org_id = org_db
    run_id = uuid.uuid4().hex
    recent_started = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=30)
    ).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, started_at) VALUES (?, ?, 'test_wf', 'running', ?)",
        (run_id, org_id, recent_started),
    )
    conn.commit()

    evaluate_alerts(conn, org_id=org_id)
    rows = [r for r in list_alerts(conn, org_id=org_id, severity="warning")
            if r["alert_type"] == "stuck_run"]
    assert len(rows) == 0


def test_timed_out_run_no_double_alert(org_db):
    """No stuck_run alert for a run in 'timed_out' status (only 'running' is checked)."""
    conn, org_id = org_db
    run_id = uuid.uuid4().hex
    old_started = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=5)
    ).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, started_at) VALUES (?, ?, 'test_wf', 'timed_out', ?)",
        (run_id, org_id, old_started),
    )
    conn.commit()

    evaluate_alerts(conn, org_id=org_id)
    rows = [r for r in list_alerts(conn, org_id=org_id, severity="warning")
            if r["alert_type"] == "stuck_run"]
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# upsert_alert_config cooldown_hours
# ---------------------------------------------------------------------------

def test_upsert_alert_config_sets_cooldown_hours(org_db):
    conn, org_id = org_db
    upsert_alert_config(conn, org_id, cooldown_hours=2)
    cfg = get_alert_config(conn, org_id)
    assert cfg["cooldown_hours"] == 2


def test_upsert_alert_config_preserves_cooldown_on_none(org_db):
    conn, org_id = org_db
    upsert_alert_config(conn, org_id, cooldown_hours=6)
    upsert_alert_config(conn, org_id, cooldown_hours=None)
    cfg = get_alert_config(conn, org_id)
    assert cfg["cooldown_hours"] == 6


def test_upsert_alert_config_cooldown_zero_stores_zero(org_db):
    conn, org_id = org_db
    upsert_alert_config(conn, org_id, cooldown_hours=0)
    cfg = get_alert_config(conn, org_id)
    assert cfg["cooldown_hours"] == 0


def test_mark_delivery_failed_sets_error(org_db):
    """mark_delivery_failed persists error string on the new alert row."""
    conn, org_id = org_db
    create_alert(conn, "test_type", "info", "Fail", org_id=org_id, dedup_key="fail:dk1")
    mark_delivery_failed(conn, "fail:dk1", "connection refused")
    row = conn.execute(
        "SELECT last_delivery_error FROM alerts WHERE dedup_key='fail:dk1'"
    ).fetchone()
    assert row["last_delivery_error"] == "connection refused"


def test_mark_delivery_failed_truncates_at_500(org_db):
    """mark_delivery_failed truncates error strings longer than 500 chars."""
    conn, org_id = org_db
    create_alert(conn, "test_type", "info", "Fail2", org_id=org_id, dedup_key="fail:dk_trunc")
    long_error = "x" * 600
    mark_delivery_failed(conn, "fail:dk_trunc", long_error)
    row = conn.execute(
        "SELECT last_delivery_error FROM alerts WHERE dedup_key='fail:dk_trunc'"
    ).fetchone()
    assert len(row["last_delivery_error"]) == 500


def test_deliver_stamps_error_on_discord_failure(org_db):
    """_deliver records last_delivery_error when Discord webhook call fails."""
    conn, org_id = org_db
    upsert_alert_config(conn, org_id, discord_webhook_url="https://discord.com/fake", min_severity="info")
    create_alert(conn, "test_type", "info", "Err", org_id=org_id, dedup_key="fail:dk2")
    with patch("urllib.request.urlopen", side_effect=Exception("webhook 500")):
        _deliver(conn, org_id, "info", "test", dedup_key="fail:dk2")
    row = conn.execute(
        "SELECT last_delivery_error FROM alerts WHERE dedup_key='fail:dk2'"
    ).fetchone()
    assert row["last_delivery_error"] is not None
    assert "webhook 500" in row["last_delivery_error"]


def test_deliver_clears_error_on_success(org_db):
    """_deliver clears last_delivery_error when delivery succeeds."""
    conn, org_id = org_db
    upsert_alert_config(conn, org_id, discord_webhook_url="https://discord.com/fake", min_severity="info")
    create_alert(conn, "test_type", "info", "Retry", org_id=org_id, dedup_key="fail:dk3")
    # Seed a prior error
    mark_delivery_failed(conn, "fail:dk3", "prior failure")
    # Now deliver successfully
    with patch("urllib.request.urlopen"):
        _deliver(conn, org_id, "info", "retry message", dedup_key="fail:dk3")
    row = conn.execute(
        "SELECT last_delivery_error, last_delivered_at FROM alerts WHERE dedup_key='fail:dk3'"
    ).fetchone()
    assert row["last_delivery_error"] is None
    assert row["last_delivered_at"] is not None


def test_workflow_failures_crash_does_not_abort_regression_check(org_db):
    """A1: _check_workflow_failures crash must not prevent _check_discord_pulse_regression
    from running. Both are individually try/except isolated in evaluate_alerts()."""
    from unittest.mock import patch
    conn, org_id = org_db

    regression_called: list[bool] = []

    def fake_regression(conn, oid):
        regression_called.append(True)
        return []

    with patch(
        "sable_platform.workflows.alert_evaluator._check_workflow_failures",
        side_effect=RuntimeError("simulated crash"),
    ), patch(
        "sable_platform.workflows.alert_evaluator._check_discord_pulse_regression",
        side_effect=fake_regression,
    ):
        evaluate_alerts(conn, org_id=org_id)

    assert regression_called, "_check_discord_pulse_regression must run even when _check_workflow_failures crashes"


def test_alerts_config_set_cooldown_hours(tmp_path, monkeypatch):
    """CLI --cooldown-hours writes cooldown_hours to alert_configs."""
    import sqlite3 as _sqlite3
    from sable_platform.db.connection import ensure_schema, get_db
    from sable_platform.cli.alert_cmds import alerts_config_set
    from click.testing import CliRunner

    db_path = str(tmp_path / "sable.db")
    conn0 = _sqlite3.connect(db_path)
    conn0.row_factory = _sqlite3.Row
    conn0.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn0)
    conn0.execute("INSERT INTO orgs (org_id, display_name) VALUES ('cli_org', 'CLI Org')")
    conn0.commit()
    conn0.close()

    monkeypatch.setattr("sable_platform.cli.alert_cmds.get_db", lambda: get_db(db_path))

    runner = CliRunner()
    result = runner.invoke(alerts_config_set, ["--org", "cli_org", "--cooldown-hours", "2"])
    assert result.exit_code == 0

    conn1 = _sqlite3.connect(db_path)
    conn1.row_factory = _sqlite3.Row
    cfg = conn1.execute("SELECT cooldown_hours FROM alert_configs WHERE org_id='cli_org'").fetchone()
    conn1.close()
    assert cfg["cooldown_hours"] == 2
