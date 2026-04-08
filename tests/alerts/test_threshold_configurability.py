"""Tests for A5: per-org config_json overrides for alert staleness thresholds."""
from __future__ import annotations

import datetime
import json

import pytest

from sable_platform.db.connection import ensure_schema
from sable_platform.workflows.alert_checks import (
    TRACKING_STALE_DAYS,
    DISCORD_PULSE_STALE_DAYS,
    STUCK_RUN_THRESHOLD_HOURS,
    _check_tracking_stale,
    _check_discord_pulse_stale,
    _check_stuck_runs,
)


@pytest.fixture
def db():
    from tests.conftest import make_test_conn
    conn = make_test_conn(with_org="test_org")
    return conn


def _set_org_config(conn, org_id: str, cfg: dict) -> None:
    conn.execute(
        "UPDATE orgs SET config_json=? WHERE org_id=?",
        (json.dumps(cfg), org_id),
    )
    conn.commit()


def _insert_sync_run(conn, org_id: str, completed_at: str) -> None:
    conn.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status, completed_at) "
        "VALUES (?, 'sable_tracking', 'completed', ?)",
        (org_id, completed_at),
    )
    conn.commit()


def _insert_pulse_run(conn, org_id: str, run_date: str) -> None:
    conn.execute(
        "INSERT INTO discord_pulse_runs (org_id, project_slug, run_date) VALUES (?, 'test', ?)",
        (org_id, run_date),
    )
    conn.commit()


def _insert_running_workflow(conn, org_id: str, started_at: str) -> str:
    from sable_platform.db.workflow_store import create_workflow_run
    run_id = create_workflow_run(conn, org_id, "test_wf", "1.0", {})
    conn.execute(
        "UPDATE workflow_runs SET status='running', started_at=? WHERE run_id=?",
        (started_at, run_id),
    )
    conn.commit()
    return run_id


# ---------------------------------------------------------------------------
# TRACKING_STALE_DAYS
# ---------------------------------------------------------------------------

def test_tracking_stale_uses_default_threshold(db):
    """Without config_json override, module constant TRACKING_STALE_DAYS is used."""
    # sync completed just inside the default window — should NOT fire
    safe_ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=TRACKING_STALE_DAYS - 1)
    ).strftime("%Y-%m-%d %H:%M:%S")
    _insert_sync_run(db, "test_org", safe_ts)

    result = _check_tracking_stale(db, "test_org")
    assert result == [], "Should not alert when sync is within default staleness window"


def test_tracking_stale_fires_with_default_threshold(db):
    """Without config_json override, alert fires when sync exceeds TRACKING_STALE_DAYS."""
    stale_ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=TRACKING_STALE_DAYS + 2)
    ).strftime("%Y-%m-%d %H:%M:%S")
    _insert_sync_run(db, "test_org", stale_ts)

    result = _check_tracking_stale(db, "test_org")
    assert len(result) == 1, "Should alert when sync exceeds default staleness window"


def test_tracking_stale_override_raises_threshold(db):
    """config_json override for tracking_stale_days extends the window; no alert fires."""
    # sync completed 20 days ago — stale by default (14), but we raise override to 30
    stale_by_default_ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=20)
    ).strftime("%Y-%m-%d %H:%M:%S")
    _insert_sync_run(db, "test_org", stale_by_default_ts)
    _set_org_config(db, "test_org", {"tracking_stale_days": 30})

    result = _check_tracking_stale(db, "test_org")
    assert result == [], "Override of 30 days should suppress alert for 20-day-old sync"


def test_tracking_stale_override_lowers_threshold(db):
    """config_json override for tracking_stale_days tightens the window; alert fires sooner."""
    # sync completed 5 days ago — NOT stale by default (14), but override to 3
    recent_ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=5)
    ).strftime("%Y-%m-%d %H:%M:%S")
    _insert_sync_run(db, "test_org", recent_ts)
    _set_org_config(db, "test_org", {"tracking_stale_days": 3})

    result = _check_tracking_stale(db, "test_org")
    assert len(result) == 1, "Override of 3 days should fire alert for 5-day-old sync"


# ---------------------------------------------------------------------------
# DISCORD_PULSE_STALE_DAYS
# ---------------------------------------------------------------------------

def test_discord_pulse_stale_override_raises_threshold(db):
    """config_json override for discord_pulse_stale_days extends window; no alert fires."""
    # pulse run 10 days ago — stale by default (7), but we raise override to 14
    stale_by_default_ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)
    ).strftime("%Y-%m-%d")
    _insert_pulse_run(db, "test_org", stale_by_default_ts)
    _set_org_config(db, "test_org", {"discord_pulse_stale_days": 14})

    result = _check_discord_pulse_stale(db, "test_org")
    assert result == [], "Override of 14 days should suppress alert for 10-day-old pulse"


def test_discord_pulse_stale_override_lowers_threshold(db):
    """config_json override for discord_pulse_stale_days tightens window; alert fires sooner."""
    # pulse run 4 days ago — NOT stale by default (7), but override to 2
    recent_ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=4)
    ).strftime("%Y-%m-%d")
    _insert_pulse_run(db, "test_org", recent_ts)
    _set_org_config(db, "test_org", {"discord_pulse_stale_days": 2})

    result = _check_discord_pulse_stale(db, "test_org")
    assert len(result) == 1, "Override of 2 days should fire alert for 4-day-old pulse"


# ---------------------------------------------------------------------------
# STUCK_RUN_THRESHOLD_HOURS
# ---------------------------------------------------------------------------

def test_stuck_run_override_raises_threshold(db):
    """config_json override for stuck_run_threshold_hours extends window; no alert fires."""
    # run stuck for 3 hours — stuck by default (2h), but raise override to 6
    stuck_ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)
    ).strftime("%Y-%m-%d %H:%M:%S")
    _insert_running_workflow(db, "test_org", stuck_ts)
    _set_org_config(db, "test_org", {"stuck_run_threshold_hours": 6})

    result = _check_stuck_runs(db, "test_org")
    assert result == [], "Override of 6h should suppress alert for 3h-old run"


def test_stuck_run_override_lowers_threshold(db):
    """config_json override for stuck_run_threshold_hours tightens window; alert fires sooner."""
    # run stuck for 90 minutes — NOT stuck by default (2h), but override to 1h
    stuck_ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=90)
    ).strftime("%Y-%m-%d %H:%M:%S")
    _insert_running_workflow(db, "test_org", stuck_ts)
    _set_org_config(db, "test_org", {"stuck_run_threshold_hours": 1})

    result = _check_stuck_runs(db, "test_org")
    assert len(result) == 1, "Override of 1h should fire alert for 90-min-old run"


def test_stuck_run_uses_default_when_no_config(db):
    """Without config_json override, module constant STUCK_RUN_THRESHOLD_HOURS is used."""
    # run stuck for just under the threshold — should NOT fire
    safe_ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=STUCK_RUN_THRESHOLD_HOURS - 1)
    ).strftime("%Y-%m-%d %H:%M:%S")
    _insert_running_workflow(db, "test_org", safe_ts)

    result = _check_stuck_runs(db, "test_org")
    assert result == [], "Should not alert when run is within default stuck threshold"
