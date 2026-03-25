"""Tests for WorkflowRunner — happy path, failure, retry, resume, skip_if."""
from __future__ import annotations

import pytest

from sable_platform.errors import SableError, STEP_EXECUTION_ERROR
from sable_platform.workflows.engine import WorkflowRunner
from sable_platform.workflows.models import StepDefinition, StepResult, WorkflowDefinition
from sable_platform.db.workflow_store import get_workflow_run, get_workflow_steps, get_workflow_events


def _ok_step(name: str, output: dict | None = None) -> StepDefinition:
    out = output or {}
    return StepDefinition(name=name, fn=lambda ctx: StepResult("completed", out), max_retries=0)


def _fail_step(name: str) -> StepDefinition:
    def fn(ctx):
        raise ValueError(f"Step {name} intentionally failed")
    return StepDefinition(name=name, fn=fn, max_retries=0)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_happy_path_completes(wf_db):
    defn = WorkflowDefinition(
        name="test_happy", version="1.0",
        steps=[_ok_step("step_a"), _ok_step("step_b"), _ok_step("step_c")],
    )
    runner = WorkflowRunner(defn)
    run_id = runner.run("wf_org", {}, conn=wf_db)

    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"

    steps = get_workflow_steps(wf_db, run_id)
    assert len(steps) == 3
    assert all(s["status"] == "completed" for s in steps)


def test_happy_path_events_emitted(wf_db):
    defn = WorkflowDefinition(
        name="test_events", version="1.0",
        steps=[_ok_step("step_a")],
    )
    runner = WorkflowRunner(defn)
    run_id = runner.run("wf_org", {}, conn=wf_db)

    events = get_workflow_events(wf_db, run_id)
    event_types = [e["event_type"] for e in events]
    assert "run_started" in event_types
    assert "step_started" in event_types
    assert "step_completed" in event_types
    assert "run_completed" in event_types


# ---------------------------------------------------------------------------
# Failure
# ---------------------------------------------------------------------------

def test_step_failure_marks_run_failed(wf_db):
    defn = WorkflowDefinition(
        name="test_fail", version="1.0",
        steps=[_ok_step("step_a"), _fail_step("step_b"), _ok_step("step_c")],
    )
    runner = WorkflowRunner(defn)

    with pytest.raises(SableError) as exc_info:
        runner.run("wf_org", {}, conn=wf_db)

    assert exc_info.value.code == STEP_EXECUTION_ERROR

    # Find the run in the DB (last workflow_run)
    runs = wf_db.execute("SELECT * FROM workflow_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    assert runs["status"] == "failed"
    assert runs["error"] is not None

    steps = get_workflow_steps(wf_db, runs["run_id"])
    step_b = next(s for s in steps if s["step_name"] == "step_b")
    assert step_b["status"] == "failed"
    assert "intentionally failed" in step_b["error"]

    # step_c should not have been executed (no row for it after failure)
    step_c_rows = [s for s in steps if s["step_name"] == "step_c"]
    assert len(step_c_rows) == 0


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------

def test_retry_on_failure(wf_db):
    """Step that fails on first attempt but succeeds on second should have retries=1."""
    call_count = {"n": 0}

    def flaky(ctx) -> StepResult:
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise ValueError("first attempt fail")
        return StepResult("completed", {"attempt": call_count["n"]})

    defn = WorkflowDefinition(
        name="test_retry", version="1.0",
        steps=[StepDefinition(name="flaky_step", fn=flaky, max_retries=2)],
    )
    runner = WorkflowRunner(defn)
    run_id = runner.run("wf_org", {}, conn=wf_db)

    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"

    steps = get_workflow_steps(wf_db, run_id)
    assert steps[0]["retries"] == 1


def test_retry_exhausted_marks_failed(wf_db):
    defn = WorkflowDefinition(
        name="test_retry_exhausted", version="1.0",
        steps=[_fail_step("always_fails")],
    )
    # StepDefinition default max_retries=0, but let's test with max_retries=1
    defn.steps[0] = StepDefinition(name="always_fails", fn=lambda ctx: (_ for _ in ()).throw(ValueError("fail")), max_retries=1)

    runner = WorkflowRunner(defn)
    with pytest.raises(SableError):
        runner.run("wf_org", {}, conn=wf_db)

    run = wf_db.execute("SELECT * FROM workflow_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    assert run["status"] == "failed"


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def test_resume_skips_completed_steps(wf_db):
    """Resume a run where step_a already completed — engine should skip it."""
    call_log: list[str] = []

    def log_step(name: str):
        def fn(ctx):
            call_log.append(name)
            return StepResult("completed", {})
        return fn

    defn = WorkflowDefinition(
        name="test_resume", version="1.0",
        steps=[
            StepDefinition(name="step_a", fn=log_step("step_a"), max_retries=0),
            StepDefinition(name="step_b", fn=log_step("step_b"), max_retries=0),
        ],
    )
    runner = WorkflowRunner(defn)

    # First run — step_a completes, step_b fails
    step_b_should_fail = {"fail": True}
    def step_b_fn(ctx):
        if step_b_should_fail["fail"]:
            raise ValueError("step_b initial failure")
        call_log.append("step_b")
        return StepResult("completed", {})

    defn.steps[1] = StepDefinition(name="step_b", fn=step_b_fn, max_retries=0)

    with pytest.raises(SableError):
        run_id = runner.run("wf_org", {}, conn=wf_db)

    # Get the run_id that was created
    run_row = wf_db.execute("SELECT run_id FROM workflow_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    run_id = run_row["run_id"]

    # Now allow step_b to succeed
    step_b_should_fail["fail"] = False
    call_log.clear()

    defn2 = WorkflowDefinition(
        name="test_resume", version="1.0",
        steps=[
            StepDefinition(name="step_a", fn=log_step("step_a"), max_retries=0),
            StepDefinition(name="step_b", fn=step_b_fn, max_retries=0),
        ],
    )
    runner2 = WorkflowRunner(defn2)
    runner2.resume(run_id, conn=wf_db)

    # step_a should NOT have been called again
    assert "step_a" not in call_log
    assert "step_b" in call_log

    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"


# ---------------------------------------------------------------------------
# skip_if
# ---------------------------------------------------------------------------

def test_skip_if_skips_step(wf_db):
    defn = WorkflowDefinition(
        name="test_skip", version="1.0",
        steps=[
            _ok_step("step_a", output={"flag": True}),
            StepDefinition(
                name="step_b",
                fn=lambda ctx: StepResult("completed", {}),
                max_retries=0,
                skip_if=lambda ctx: ctx.input_data.get("flag") is True,
            ),
            _ok_step("step_c"),
        ],
    )
    runner = WorkflowRunner(defn)
    run_id = runner.run("wf_org", {}, conn=wf_db)

    steps = get_workflow_steps(wf_db, run_id)
    by_name = {s["step_name"]: s for s in steps}

    assert by_name["step_a"]["status"] == "completed"
    assert by_name["step_b"]["status"] == "skipped"
    assert by_name["step_c"]["status"] == "completed"


# ---------------------------------------------------------------------------
# Accumulated output propagation
# ---------------------------------------------------------------------------

def test_accumulated_output_available_to_next_step(wf_db):
    received_input: dict = {}

    def step_b(ctx) -> StepResult:
        received_input.update(ctx.input_data)
        return StepResult("completed", {})

    defn = WorkflowDefinition(
        name="test_accum", version="1.0",
        steps=[
            _ok_step("step_a", output={"key_from_a": "value_from_a"}),
            StepDefinition(name="step_b", fn=step_b, max_retries=0),
        ],
    )
    runner = WorkflowRunner(defn)
    runner.run("wf_org", {}, conn=wf_db)

    assert received_input.get("key_from_a") == "value_from_a"
