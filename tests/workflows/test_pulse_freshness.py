"""Tests for _check_pulse_freshness dual-source (sync_runs + artifacts)."""
from __future__ import annotations

import datetime
import json
from unittest.mock import MagicMock

import pytest

from sable_platform.workflows.builtins.weekly_client_loop import _check_pulse_freshness


def _ts(days_ago: int) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def test_fresh_from_sync_runs(wf_db):
    """Pulse is fresh when sync_runs has a recent pulse_track entry."""
    wf_db.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status, completed_at) VALUES ('wf_org', 'pulse_track', 'completed', ?)",
        (_ts(2),),
    )
    wf_db.commit()

    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.config = {}

    result = _check_pulse_freshness(ctx)
    assert result.output["pulse_fresh"] is True
    assert result.output["pulse_age_days"] <= 3


def test_fresh_from_artifacts(wf_db):
    """Pulse is fresh when artifacts has a recent pulse_report."""
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path, stale, created_at) VALUES ('wf_org', 'pulse_report', '/fake', 0, ?)",
        (_ts(3),),
    )
    wf_db.commit()

    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.config = {}

    result = _check_pulse_freshness(ctx)
    assert result.output["pulse_fresh"] is True


def test_uses_most_recent_of_both(wf_db):
    """When both sources exist, uses the more recent one."""
    # Old artifact
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path, stale, created_at) VALUES ('wf_org', 'pulse_report', '/fake', 0, ?)",
        (_ts(20),),
    )
    # Recent sync_run
    wf_db.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status, completed_at) VALUES ('wf_org', 'meta_scan', 'completed', ?)",
        (_ts(1),),
    )
    wf_db.commit()

    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.config = {}

    result = _check_pulse_freshness(ctx)
    assert result.output["pulse_fresh"] is True
    assert result.output["pulse_age_days"] <= 2


def test_stale_when_no_data(wf_db):
    """Pulse is stale when neither source has data."""
    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.config = {}

    result = _check_pulse_freshness(ctx)
    assert result.output["pulse_fresh"] is False
    assert result.output["pulse_age_days"] == 999


def test_stale_when_old_data(wf_db):
    """Pulse is stale when data is older than threshold."""
    wf_db.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status, completed_at) VALUES ('wf_org', 'pulse_track', 'completed', ?)",
        (_ts(30),),
    )
    wf_db.commit()

    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.config = {"pulse_staleness_days": 14}

    result = _check_pulse_freshness(ctx)
    assert result.output["pulse_fresh"] is False


def test_ignores_failed_sync_runs(wf_db):
    """Only considers completed sync_runs, not failed ones."""
    wf_db.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status, completed_at) VALUES ('wf_org', 'pulse_track', 'failed', ?)",
        (_ts(1),),
    )
    wf_db.commit()

    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.config = {}

    result = _check_pulse_freshness(ctx)
    assert result.output["pulse_fresh"] is False
    assert result.output["pulse_age_days"] == 999
