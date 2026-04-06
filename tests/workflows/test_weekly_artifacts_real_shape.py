"""Tests that _register_artifacts works with the real SlopperAdvisoryAdapter return shape.

The real adapter returns {status, job_ref, org_id} — no 'artifacts' key.
_register_artifacts must query the artifacts table directly, not parse adapter output.
"""
from __future__ import annotations

import datetime
import json
from unittest.mock import MagicMock, patch

import pytest

from sable_platform.workflows.builtins.weekly_client_loop import (
    _register_artifacts,
    _mark_complete,
    _trigger_strategy_generation,
    WEEKLY_CLIENT_LOOP,
)
from sable_platform.workflows.engine import WorkflowRunner
from sable_platform.db.workflow_store import get_workflow_steps


_TEST_RUN_ID = "test_run"


def _ensure_run_row(conn, run_id=_TEST_RUN_ID, org_id="wf_org"):
    """Insert a workflow_runs row with started_at in the past so artifact queries are scoped."""
    started = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
    ).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT OR IGNORE INTO workflow_runs (run_id, org_id, workflow_name, status, started_at) "
        "VALUES (?, ?, 'weekly_client_loop', 'running', ?)",
        (run_id, org_id, started),
    )
    conn.commit()


def test_register_artifacts_with_real_adapter_shape(wf_db):
    """_register_artifacts succeeds when strategy_result has no 'artifacts' key."""
    _ensure_run_row(wf_db)
    # Insert an artifact as Slopper would (directly in DB, not via adapter return)
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path, stale) VALUES ('wf_org', 'twitter_strategy_brief', '/tmp/brief.md', 0)"
    )
    wf_db.commit()

    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.run_id = _TEST_RUN_ID
    # Real adapter shape: no 'artifacts' key
    ctx.input_data = {"strategy_result": {"status": "completed", "job_ref": "wf_org", "org_id": "wf_org"}}

    result = _register_artifacts(ctx)
    assert result.output["artifact_count"] >= 1
    assert len(result.output["artifact_ids"]) >= 1


def test_register_artifacts_empty_when_no_artifacts(wf_db):
    """_register_artifacts returns 0 when org has no artifacts."""
    _ensure_run_row(wf_db)
    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.run_id = _TEST_RUN_ID
    ctx.input_data = {}

    result = _register_artifacts(ctx)
    assert result.output["artifact_count"] == 0
    assert result.output["artifact_ids"] == []


def test_register_artifacts_excludes_stale(wf_db):
    """_register_artifacts only counts non-stale artifacts."""
    _ensure_run_row(wf_db)
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path, stale) VALUES ('wf_org', 'twitter_strategy_brief', '/tmp/old.md', 1)"
    )
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path, stale) VALUES ('wf_org', 'twitter_strategy_brief', '/tmp/new.md', 0)"
    )
    wf_db.commit()

    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.run_id = _TEST_RUN_ID
    ctx.input_data = {}

    result = _register_artifacts(ctx)
    assert result.output["artifact_count"] == 1


def test_mark_complete_artifact_count_matches_db(wf_db):
    """mark_complete.summary.artifact_count reflects real artifacts in DB."""
    _ensure_run_row(wf_db)
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path, stale) VALUES ('wf_org', 'twitter_strategy_brief', '/tmp/brief.md', 0)"
    )
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path, stale) VALUES ('wf_org', 'discord_playbook', '/tmp/playbook.md', 0)"
    )
    wf_db.commit()

    # Run _register_artifacts first to get the count
    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.run_id = _TEST_RUN_ID
    ctx.input_data = {}

    art_result = _register_artifacts(ctx)

    # Now run _mark_complete with that count propagated
    ctx.input_data = {
        "artifact_count": art_result.output["artifact_count"],
        "actions_created": 0,
        "tracking_fresh": True,
        "tracking_age_days": 1,
        "pulse_fresh": True,
        "pulse_age_days": 2,
    }
    complete_result = _mark_complete(ctx)
    assert complete_result.output["summary"]["artifact_count"] == 2


def test_full_workflow_with_real_adapter_shape(wf_db):
    """Full workflow run with mocked adapters using real return shapes.

    Tracking must appear fresh so mark_stale_artifacts is skipped (otherwise it
    stales the pre-seeded artifact before _register_artifacts reads it).
    """
    import datetime

    def tracking_run(self, input_data):
        return {"status": "completed", "job_ref": input_data.get("org_id")}

    def slopper_run(self, input_data):
        # Real shape: no 'artifacts' key
        return {"status": "completed", "job_ref": input_data.get("org_id"), "org_id": input_data.get("org_id")}

    # Fresh tracking sync so mark_stale_artifacts is skipped
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    wf_db.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status, completed_at) VALUES ('wf_org', 'sable_tracking', 'completed', ?)",
        (now,),
    )
    # Pre-seed a strategy brief artifact (as Slopper would write directly)
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path, stale) VALUES ('wf_org', 'twitter_strategy_brief', '/tmp/brief.md', 0)"
    )
    wf_db.commit()

    with patch("sable_platform.adapters.tracking_sync.SableTrackingAdapter.run", tracking_run), \
         patch("sable_platform.adapters.slopper.SlopperAdvisoryAdapter.run", slopper_run):
        runner = WorkflowRunner(WEEKLY_CLIENT_LOOP)
        run_id = runner.run("wf_org", {"org_id": "wf_org"}, conn=wf_db)

    steps = {s["step_name"]: s for s in get_workflow_steps(wf_db, run_id)}
    art_output = json.loads(steps["register_artifacts"]["output_json"] or "{}")
    assert art_output["artifact_count"] >= 1

    complete_output = json.loads(steps["mark_complete"]["output_json"] or "{}")
    assert complete_output["summary"]["artifact_count"] >= 1
