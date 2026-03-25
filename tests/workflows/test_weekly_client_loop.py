"""Integration tests for Workflow 2: weekly_client_loop."""
from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest

from sable_platform.workflows.engine import WorkflowRunner
from sable_platform.workflows.builtins.weekly_client_loop import WEEKLY_CLIENT_LOOP
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
