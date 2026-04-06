"""Integration tests for Workflow 2: weekly_client_loop."""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from sable_platform.workflows.engine import WorkflowRunner
from sable_platform.workflows.builtins.weekly_client_loop import (
    WEEKLY_CLIENT_LOOP,
    _register_artifacts,
    _parse_actions_from_artifact,
)
from sable_platform.db.workflow_store import get_workflow_run, get_workflow_steps


def _ts(days_ago: int) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def mock_adapters():
    """Mock tracking and slopper adapters to avoid subprocess calls."""
    def tracking_run(self, input_data):
        return {"status": "completed", "job_ref": input_data.get("org_id")}

    def slopper_run(self, input_data):
        return {"status": "completed", "job_ref": input_data.get("org_id"), "artifacts": []}

    with patch("sable_platform.adapters.tracking_sync.SableTrackingAdapter.run", tracking_run), \
         patch("sable_platform.adapters.slopper.SlopperAdvisoryAdapter.run", slopper_run):
        yield


def test_stale_tracking_triggers_sync(wf_db, mock_adapters):
    """When tracking sync is 8 days old, trigger_tracking_sync should NOT be skipped."""
    # Insert an 8-day-old sync run
    wf_db.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status, completed_at) VALUES ('wf_org', 'sable_tracking', 'completed', ?)",
        (_ts(8),),
    )
    wf_db.commit()

    runner = WorkflowRunner(WEEKLY_CLIENT_LOOP)
    run_id = runner.run("wf_org", {"org_id": "wf_org"}, conn=wf_db)

    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"

    steps = {s["step_name"]: s["status"] for s in get_workflow_steps(wf_db, run_id)}
    assert steps.get("trigger_tracking_sync") == "completed", "Expected tracking sync to run"
    assert steps.get("mark_stale_artifacts") == "completed"


def test_fresh_tracking_skips_sync(wf_db, mock_adapters):
    """When tracking sync is 3 days old, trigger_tracking_sync should be skipped."""
    wf_db.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status, completed_at) VALUES ('wf_org', 'sable_tracking', 'completed', ?)",
        (_ts(3),),
    )
    wf_db.commit()

    runner = WorkflowRunner(WEEKLY_CLIENT_LOOP)
    run_id = runner.run("wf_org", {"org_id": "wf_org"}, conn=wf_db)

    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"

    steps = {s["step_name"]: s["status"] for s in get_workflow_steps(wf_db, run_id)}
    assert steps.get("trigger_tracking_sync") == "skipped", "Expected tracking sync to be skipped"
    assert steps.get("mark_stale_artifacts") == "skipped"


def test_no_tracking_data_triggers_sync(wf_db, mock_adapters):
    """When there is no tracking sync history, sync should run."""
    runner = WorkflowRunner(WEEKLY_CLIENT_LOOP)
    run_id = runner.run("wf_org", {"org_id": "wf_org"}, conn=wf_db)

    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"

    steps = {s["step_name"]: s["status"] for s in get_workflow_steps(wf_db, run_id)}
    assert steps.get("trigger_tracking_sync") == "completed"


def test_freshness_output_in_step(wf_db, mock_adapters):
    """check_tracking_freshness output should include tracking_fresh and age_days."""
    import json

    wf_db.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status, completed_at) VALUES ('wf_org', 'sable_tracking', 'completed', ?)",
        (_ts(2),),
    )
    wf_db.commit()

    runner = WorkflowRunner(WEEKLY_CLIENT_LOOP)
    run_id = runner.run("wf_org", {"org_id": "wf_org"}, conn=wf_db)

    steps = {s["step_name"]: s for s in get_workflow_steps(wf_db, run_id)}
    freshness_step = steps["check_tracking_freshness"]
    output = json.loads(freshness_step["output_json"] or "{}")

    assert output["tracking_fresh"] is True
    assert output["tracking_age_days"] <= 3


# ---------------------------------------------------------------------------
# Run-scoped artifact isolation
# ---------------------------------------------------------------------------

def test_register_artifacts_scoped_to_current_run(wf_db):
    """_register_artifacts only counts artifacts created after the current run started."""
    import json
    import uuid

    org_id = "wf_org"

    # Simulate a completed first run that produced artifacts 2 hours ago
    old_run_id = uuid.uuid4().hex
    two_hours_ago = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)
    ).strftime("%Y-%m-%d %H:%M:%S")
    wf_db.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, started_at, completed_at) "
        "VALUES (?, ?, 'weekly_client_loop', 'completed', ?, ?)",
        (old_run_id, org_id, two_hours_ago, two_hours_ago),
    )
    # Insert an artifact from that old run
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path, created_at) VALUES (?, 'pulse_report', '/tmp/old.md', ?)",
        (org_id, two_hours_ago),
    )
    wf_db.commit()

    # Create a new "current" run that just started
    new_run_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    wf_db.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, started_at) "
        "VALUES (?, ?, 'weekly_client_loop', 'running', ?)",
        (new_run_id, org_id, now),
    )
    wf_db.commit()

    # Build a minimal StepContext
    ctx = type("Ctx", (), {
        "run_id": new_run_id,
        "org_id": org_id,
        "db": wf_db,
        "input_data": {},
    })()

    result = _register_artifacts(ctx)
    assert result.output["artifact_count"] == 0, (
        "Second run should not see artifacts from the first run"
    )


def test_parse_actions_scoped_to_current_run(wf_db, tmp_path):
    """_parse_actions_from_artifact ignores artifacts from before the current run."""
    import uuid

    org_id = "wf_org"

    two_hours_ago = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)
    ).strftime("%Y-%m-%d %H:%M:%S")

    # Create an old artifact with actionable content
    playbook = tmp_path / "old_playbook.md"
    playbook.write_text("## Actions\n- Do the old thing\n- Do another old thing\n")

    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path, created_at) VALUES (?, 'discord_playbook', ?, ?)",
        (org_id, str(playbook), two_hours_ago),
    )
    wf_db.commit()

    # Create a current run that started after the artifact was created
    new_run_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    wf_db.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, started_at) "
        "VALUES (?, ?, 'weekly_client_loop', 'running', ?)",
        (new_run_id, org_id, now),
    )
    wf_db.commit()

    ctx = type("Ctx", (), {
        "run_id": new_run_id,
        "org_id": org_id,
        "db": wf_db,
        "input_data": {},
    })()

    action_ids = _parse_actions_from_artifact(ctx, "discord_playbook", "playbook", "general")
    assert action_ids == [], "Should not parse actions from pre-run artifacts"
