"""Tests for Operator Action Layer (Feature 1)."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from sable_platform.db.actions import (
    create_action,
    claim_action,
    complete_action,
    skip_action,
    get_action,
    list_actions,
    action_summary,
)
from sable_platform.errors import SableError
from sable_platform.workflows.engine import WorkflowRunner
from sable_platform.workflows.builtins.weekly_client_loop import WEEKLY_CLIENT_LOOP
from sable_platform.db.workflow_store import get_workflow_run, get_workflow_steps


# ---------------------------------------------------------------------------
# Core action CRUD
# ---------------------------------------------------------------------------

def test_create_action_returns_uuid(org_db):
    conn, org_id = org_db
    action_id = create_action(conn, org_id, "DM @example about growth")
    assert action_id
    assert len(action_id) == 32  # uuid4 hex


def test_create_action_default_status(org_db):
    conn, org_id = org_db
    action_id = create_action(conn, org_id, "Post the clip")
    row = get_action(conn, action_id)
    assert row["status"] == "pending"
    assert row["org_id"] == org_id
    assert row["title"] == "Post the clip"


def test_claim_action(org_db):
    conn, org_id = org_db
    action_id = create_action(conn, org_id, "Reply to thread")
    claim_action(conn, action_id, "alice")
    row = get_action(conn, action_id)
    assert row["status"] == "claimed"
    assert row["operator"] == "alice"
    assert row["claimed_at"] is not None


def test_complete_action(org_db):
    conn, org_id = org_db
    action_id = create_action(conn, org_id, "Run AMA")
    claim_action(conn, action_id, "bob")
    complete_action(conn, action_id, outcome_notes="Went well, 40 attendees")
    row = get_action(conn, action_id)
    assert row["status"] == "completed"
    assert row["completed_at"] is not None
    assert row["outcome_notes"] == "Went well, 40 attendees"


def test_skip_action(org_db):
    conn, org_id = org_db
    action_id = create_action(conn, org_id, "Low priority thing")
    skip_action(conn, action_id, outcome_notes="not relevant this week")
    row = get_action(conn, action_id)
    assert row["status"] == "skipped"
    assert row["skipped_at"] is not None


def test_get_action_missing_raises(org_db):
    conn, _ = org_db
    with pytest.raises(SableError):
        get_action(conn, "nonexistent_id")


def test_list_actions_filter_by_status(org_db):
    conn, org_id = org_db
    a1 = create_action(conn, org_id, "Pending action")
    a2 = create_action(conn, org_id, "Another pending")
    complete_action(conn, a2)
    pending = list_actions(conn, org_id, status="pending")
    assert len(pending) == 1
    assert pending[0]["action_id"] == a1


def test_list_actions_no_filter(org_db):
    conn, org_id = org_db
    create_action(conn, org_id, "A")
    create_action(conn, org_id, "B")
    complete_action(conn, create_action(conn, org_id, "C"))
    rows = list_actions(conn, org_id)
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# Action summary
# ---------------------------------------------------------------------------

def test_action_summary_execution_rate(org_db):
    conn, org_id = org_db
    a1 = create_action(conn, org_id, "Done 1")
    a2 = create_action(conn, org_id, "Done 2")
    a3 = create_action(conn, org_id, "Skipped")
    a4 = create_action(conn, org_id, "Pending")

    complete_action(conn, a1)
    complete_action(conn, a2)
    skip_action(conn, a3)
    # a4 stays pending

    s = action_summary(conn, org_id)
    assert s["completed"] == 2
    assert s["skipped"] == 1
    assert s["pending"] == 1
    # execution_rate = completed / (completed + skipped + pending) = 2/4 = 0.5
    assert abs(s["execution_rate"] - 0.5) < 0.01


def test_action_summary_empty_org(org_db):
    conn, org_id = org_db
    s = action_summary(conn, org_id)
    assert s["total"] == 0
    assert s["execution_rate"] == 0.0


# ---------------------------------------------------------------------------
# Weekly client loop: register_actions step
# ---------------------------------------------------------------------------

def _mock_adapters():
    def tracking_run(self, input_data):
        return {"status": "completed", "job_ref": input_data.get("org_id")}

    def slopper_run(self, input_data):
        return {"status": "completed", "job_ref": input_data.get("org_id"), "artifacts": []}

    return (
        patch("sable_platform.adapters.tracking_sync.SableTrackingAdapter.run", tracking_run),
        patch("sable_platform.adapters.slopper.SlopperAdvisoryAdapter.run", slopper_run),
    )


def test_register_actions_from_playbook(org_db):
    """register_actions step extracts action items from a discord_playbook artifact."""
    conn, org_id = org_db

    # Create a fake playbook file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("## Discord Playbook\n\n")
        f.write("### Recommendations\n\n")
        f.write("- DM @example about weekly AMA\n")
        f.write("- Reply to @person's tweet thread\n")
        f.write("- Post the clip to the main account\n")
        f.write("\n## Other stuff\n")
        f.write("- This should not be an action\n")
        playbook_path = f.name

    try:
        # Insert the artifact into the DB
        conn.execute(
            "INSERT INTO artifacts (org_id, artifact_type, path, metadata_json) VALUES (?, ?, ?, '{}')",
            (org_id, "discord_playbook", playbook_path),
        )
        conn.commit()

        p1 = patch("sable_platform.adapters.tracking_sync.SableTrackingAdapter.run",
                   lambda self, d: {"status": "completed", "job_ref": d.get("org_id")})
        p2 = patch("sable_platform.adapters.slopper.SlopperAdvisoryAdapter.run",
                   lambda self, d: {"status": "completed", "job_ref": d.get("org_id"), "artifacts": []})
        with p1, p2:
            runner = WorkflowRunner(WEEKLY_CLIENT_LOOP)
            runner.run(org_id, {"org_id": org_id}, conn=conn)

        actions = list_actions(conn, org_id)
        assert len(actions) == 3
        titles = {a["title"] for a in actions}
        assert any("DM @example" in t for t in titles)
        assert all(a["source"] == "playbook" for a in actions)
    finally:
        os.unlink(playbook_path)


def test_register_actions_no_playbook_graceful(org_db):
    """register_actions step completes without error when no playbook exists."""
    conn, org_id = org_db

    p1 = patch("sable_platform.adapters.tracking_sync.SableTrackingAdapter.run",
               lambda self, d: {"status": "completed", "job_ref": d.get("org_id")})
    p2 = patch("sable_platform.adapters.slopper.SlopperAdvisoryAdapter.run",
               lambda self, d: {"status": "completed", "job_ref": d.get("org_id"), "artifacts": []})
    with p1, p2:
        runner = WorkflowRunner(WEEKLY_CLIENT_LOOP)
        run_id = runner.run(org_id, {"org_id": org_id}, conn=conn)

    run = get_workflow_run(conn, run_id)
    assert run["status"] == "completed"
    steps = {s["step_name"]: s["status"] for s in get_workflow_steps(conn, run_id)}
    assert steps["register_actions"] == "completed"
    assert list_actions(conn, org_id) == []
