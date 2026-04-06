"""Tests for onboard_client builtin workflow."""
from __future__ import annotations

import os
import pytest

from sable_platform.workflows.engine import WorkflowRunner
from sable_platform.workflows.builtins.onboard_client import ONBOARD_CLIENT
from sable_platform.db.workflow_store import get_workflow_run


def test_onboard_client_completes_all_adapters_missing(wf_db):
    """Workflow completes even when all adapter env vars are missing."""
    env_vars = ["SABLE_TRACKING_PATH", "SABLE_SLOPPER_PATH", "SABLE_CULT_GRADER_PATH",
                "SABLE_LEAD_IDENTIFIER_PATH"]

    runner = WorkflowRunner(ONBOARD_CLIENT)
    # Unset env vars so adapter checks fail gracefully
    original = {v: os.environ.pop(v, None) for v in env_vars}
    try:
        run_id = runner.run("wf_org", {}, conn=wf_db)
    finally:
        for v, val in original.items():
            if val is not None:
                os.environ[v] = val

    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"

    # tools_failed should list all four adapters
    steps = wf_db.execute(
        "SELECT output_json FROM workflow_steps WHERE step_name='mark_complete'"
    ).fetchone()
    import json
    summary = json.loads(steps["output_json"])["summary"]
    assert len(summary["tools_failed"]) == 4
    assert summary["sync_run_id"] is not None


def test_onboard_client_includes_lead_identifier_in_tools_failed(wf_db):
    """lead_identifier must appear in tools_failed when SABLE_LEAD_IDENTIFIER_PATH is unset."""
    env_vars = ["SABLE_TRACKING_PATH", "SABLE_SLOPPER_PATH", "SABLE_CULT_GRADER_PATH",
                "SABLE_LEAD_IDENTIFIER_PATH"]
    original = {v: os.environ.pop(v, None) for v in env_vars}
    try:
        run_id = WorkflowRunner(ONBOARD_CLIENT).run("wf_org", {}, conn=wf_db)
    finally:
        for v, val in original.items():
            if val is not None:
                os.environ[v] = val

    import json
    steps = wf_db.execute(
        "SELECT output_json FROM workflow_steps WHERE step_name='mark_complete'"
    ).fetchone()
    summary = json.loads(steps["output_json"])["summary"]
    assert "lead_identifier" in summary["tools_failed"]


def test_onboard_client_creates_sync_run_row(wf_db):
    """create_initial_sync_record step must insert a sync_runs row."""
    runner = WorkflowRunner(ONBOARD_CLIENT)
    original = {v: os.environ.pop(v, None) for v in ["SABLE_TRACKING_PATH", "SABLE_SLOPPER_PATH", "SABLE_CULT_GRADER_PATH"]}
    try:
        runner.run("wf_org", {}, conn=wf_db)
    finally:
        for v, val in original.items():
            if val is not None:
                os.environ[v] = val

    row = wf_db.execute(
        "SELECT * FROM sync_runs WHERE org_id='wf_org' AND sync_type='onboarding'"
    ).fetchone()
    assert row is not None
    assert row["status"] == "pending"


def test_onboard_client_fails_for_unknown_org(wf_db):
    """verify_org step must raise SableError for a non-existent org."""
    from sable_platform.errors import SableError
    runner = WorkflowRunner(ONBOARD_CLIENT)
    with pytest.raises(SableError):
        runner.run("nonexistent_org_xyz", {}, conn=wf_db)
