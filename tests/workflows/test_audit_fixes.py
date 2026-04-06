"""Tests for post-v0.5 audit fixes: C1, C2, C3, D1, D2."""
from __future__ import annotations

import json
import sqlite3

import pytest

from sable_platform.db.connection import ensure_schema
from sable_platform.db.workflow_store import (
    create_workflow_run,
    create_workflow_step,
    fail_workflow_run,
    get_workflow_run,
    get_workflow_steps,
    start_workflow_run,
    start_workflow_step,
)
from sable_platform.errors import SableError
from sable_platform.workflows.engine import WorkflowRunner
from sable_platform.workflows.models import StepDefinition, StepResult, WorkflowDefinition


def _ok_step(name: str, output: dict | None = None) -> StepDefinition:
    out = output or {}
    return StepDefinition(name=name, fn=lambda ctx: StepResult("completed", out), max_retries=0)


@pytest.fixture
def wf_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('wf_org', 'WF Test Org')")
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# C1: resume() must reset orphaned 'running' steps
# ---------------------------------------------------------------------------

def test_resume_resets_orphaned_running_step(wf_db):
    """A step stuck in 'running' from a crash must be marked failed on resume,
    then re-executed with a fresh step_id."""
    defn = WorkflowDefinition(
        name="test_c1", version="1.0",
        steps=[
            _ok_step("step_a"),
            _ok_step("step_b"),
        ],
    )
    runner = WorkflowRunner(defn)

    # Manually create a failed run with step_a 'running' (simulates mid-step crash)
    run_id = create_workflow_run(wf_db, "wf_org", "test_c1", "1.0", {})
    start_workflow_run(wf_db, run_id)
    step_a_id = create_workflow_step(wf_db, run_id, "step_a", 0, {})
    start_workflow_step(wf_db, step_a_id)  # crash — step left in 'running'
    fail_workflow_run(wf_db, run_id, "simulated crash")

    # Resume should succeed and re-execute step_a
    call_log: list[str] = []

    def log_step(name: str):
        def fn(ctx):
            call_log.append(name)
            return StepResult("completed", {})
        return fn

    defn2 = WorkflowDefinition(
        name="test_c1", version="1.0",
        steps=[
            StepDefinition(name="step_a", fn=log_step("step_a"), max_retries=0),
            StepDefinition(name="step_b", fn=log_step("step_b"), max_retries=0),
        ],
    )
    runner2 = WorkflowRunner(defn2)
    runner2.resume(run_id, conn=wf_db)

    # Both steps should have run (step_a was not in completed_names)
    assert "step_a" in call_log
    assert "step_b" in call_log

    # The orphaned 'running' step must have been reset to 'failed'
    all_steps = get_workflow_steps(wf_db, run_id)
    orphan = next((s for s in all_steps if s["step_id"] == step_a_id), None)
    assert orphan is not None
    assert orphan["status"] == "failed"
    assert "running state" in (orphan["error"] or "")

    # The run itself must be completed
    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"


# ---------------------------------------------------------------------------
# C2: webhook dispatch timeout is 1s
# ---------------------------------------------------------------------------

def test_webhook_dispatch_timeout_is_one_second():
    """urlopen must use timeout=1 to bound alert eval latency."""
    import inspect
    from sable_platform.webhooks import dispatch as dispatch_module
    source = inspect.getsource(dispatch_module._http_deliver)
    assert "timeout=1" in source
    assert "timeout=3" not in source


# ---------------------------------------------------------------------------
# C3: dispatch.py imports sqlite3
# ---------------------------------------------------------------------------

def test_dispatch_module_imports_sqlite3():
    """dispatch.py must import sqlite3 explicitly (not rely on lazy annotation eval)."""
    import importlib
    import sable_platform.webhooks.dispatch as dispatch_module
    assert hasattr(dispatch_module, "sqlite3"), (
        "dispatch.py must contain 'import sqlite3' so the type hint in "
        "dispatch_event() works outside of PEP 563 lazy evaluation"
    )


# ---------------------------------------------------------------------------
# D1: dedup keys include org_id
# ---------------------------------------------------------------------------

def test_unclaimed_dedup_key_includes_org_id(wf_db):
    """_check_action_unclaimed must scope dedup_key to org_id."""
    from sable_platform.workflows.alert_checks import _check_actions_unclaimed as _check_action_unclaimed
    import datetime

    conn = wf_db
    org_id = "wf_org"

    # Insert an action pending for >7 days
    action_id = "aaa111"
    old_ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)
    ).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO actions (action_id, org_id, title, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
        (action_id, org_id, "Old action", old_ts),
    )
    conn.commit()

    created = _check_action_unclaimed(conn, org_id)
    assert len(created) == 1

    alert = conn.execute("SELECT dedup_key FROM alerts WHERE alert_id=?", (created[0],)).fetchone()
    assert alert is not None
    key = alert["dedup_key"]
    assert key.startswith(f"unclaimed:{org_id}:"), (
        f"Expected 'unclaimed:{{org_id}}:...' but got: {key!r}"
    )


def test_workflow_failed_dedup_key_includes_org_id(wf_db):
    """_check_workflow_failures must scope dedup_key to org_id."""
    from sable_platform.workflows.alert_checks import _check_workflow_failures

    conn = wf_db
    org_id = "wf_org"

    run_id = create_workflow_run(conn, org_id, "test_wf", "1.0", {})
    fail_workflow_run(conn, run_id, "test failure")

    created = _check_workflow_failures(conn, org_id)
    assert len(created) >= 1

    alert = conn.execute("SELECT dedup_key FROM alerts WHERE alert_id=?", (created[0],)).fetchone()
    assert alert is not None
    key = alert["dedup_key"]
    assert key.startswith(f"workflow_failed:{org_id}:"), (
        f"Expected 'workflow_failed:{{org_id}}:...' but got: {key!r}"
    )


def test_stuck_run_dedup_key_includes_org_id(wf_db):
    """_check_stuck_runs must scope dedup_key to org_id."""
    from sable_platform.workflows.alert_checks import _check_stuck_runs

    conn = wf_db
    org_id = "wf_org"

    # Insert a run stuck in 'running' for >3 hours
    run_id = create_workflow_run(conn, org_id, "test_stuck_wf", "1.0", {})
    conn.execute(
        "UPDATE workflow_runs SET status='running', started_at=datetime('now', '-4 hours') WHERE run_id=?",
        (run_id,),
    )
    conn.commit()

    created = _check_stuck_runs(conn, org_id)
    assert len(created) >= 1

    alert = conn.execute("SELECT dedup_key FROM alerts WHERE alert_id=?", (created[0],)).fetchone()
    assert alert is not None
    key = alert["dedup_key"]
    assert key.startswith(f"stuck_run:{org_id}:"), (
        f"Expected 'stuck_run:{{org_id}}:...' but got: {key!r}"
    )


# ---------------------------------------------------------------------------
# D2: resume() tolerates corrupt output_json
# ---------------------------------------------------------------------------

def test_resume_tolerates_corrupt_output_json(wf_db):
    """A completed step with corrupt output_json must not crash resume().
    The corrupt step's output is skipped; all subsequent steps execute normally."""
    conn = wf_db
    org_id = "wf_org"

    # Manually build a failed run: step_a completed with corrupt output, step_b pending
    run_id = create_workflow_run(conn, org_id, "test_d2", "1.0", {})
    start_workflow_run(conn, run_id)
    step_a_id = create_workflow_step(conn, run_id, "step_a", 0, {})
    # Corrupt the output_json directly
    conn.execute(
        "UPDATE workflow_steps SET status='completed', completed_at=datetime('now'), output_json=? WHERE step_id=?",
        ("{NOT VALID JSON!!!", step_a_id),
    )
    fail_workflow_run(conn, run_id, "test failure")

    call_log: list[str] = []

    def log_step(name: str):
        def fn(ctx):
            call_log.append(name)
            return StepResult("completed", {})
        return fn

    defn = WorkflowDefinition(
        name="test_d2", version="1.0",
        steps=[
            StepDefinition(name="step_a", fn=log_step("step_a"), max_retries=0),
            StepDefinition(name="step_b", fn=log_step("step_b"), max_retries=0),
        ],
    )
    runner = WorkflowRunner(defn)
    # Should not raise despite corrupt output_json
    runner.resume(run_id, conn=conn)

    # step_a was already completed so it's skipped; step_b runs
    assert "step_a" not in call_log
    assert "step_b" in call_log

    run = get_workflow_run(conn, run_id)
    assert run["status"] == "completed"


def test_resume_tolerates_corrupt_output_json_on_skipped_step(wf_db):
    """A SKIPPED step with corrupt output_json must also not crash resume()."""
    conn = wf_db
    org_id = "wf_org"

    run_id = create_workflow_run(conn, org_id, "test_d2_skip", "1.0", {})
    start_workflow_run(conn, run_id)
    step_a_id = create_workflow_step(conn, run_id, "step_a", 0, {})
    # Corrupt the output_json on a skipped step
    conn.execute(
        "UPDATE workflow_steps SET status='skipped', completed_at=datetime('now'), output_json=? WHERE step_id=?",
        ("{NOT VALID JSON!!!", step_a_id),
    )
    fail_workflow_run(conn, run_id, "test failure")

    call_log: list[str] = []

    def log_step(name: str):
        def fn(ctx):
            call_log.append(name)
            return StepResult("completed", {})
        return fn

    defn = WorkflowDefinition(
        name="test_d2_skip", version="1.0",
        steps=[
            StepDefinition(name="step_a", fn=log_step("step_a"), max_retries=0),
            StepDefinition(name="step_b", fn=log_step("step_b"), max_retries=0),
        ],
    )
    runner = WorkflowRunner(defn)
    runner.resume(run_id, conn=conn)

    # step_a was skipped, so it's in completed_names — step_b runs
    assert "step_a" not in call_log
    assert "step_b" in call_log

    run = get_workflow_run(conn, run_id)
    assert run["status"] == "completed"
