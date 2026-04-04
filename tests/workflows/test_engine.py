"""Tests for WorkflowRunner — happy path, failure, retry, resume, skip_if."""
from __future__ import annotations

import pytest

from sable_platform.errors import SableError, STEP_EXECUTION_ERROR
from sable_platform.workflows.engine import (
    WorkflowRunner,
    _legacy_workflow_fingerprint,
    _workflow_fingerprint,
)
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


def test_resume_already_completed_raises(wf_db):
    """Resuming a fully completed run raises SableError."""
    defn = WorkflowDefinition(
        name="test_resume_done", version="1.0",
        steps=[_ok_step("only_step")],
    )
    runner = WorkflowRunner(defn)
    run_id = runner.run("wf_org", {}, conn=wf_db)

    with pytest.raises(SableError):
        runner.resume(run_id, conn=wf_db)


def test_resume_nonexistent_raises(wf_db):
    """Resuming a run_id that doesn't exist raises SableError."""
    defn = WorkflowDefinition(
        name="test_resume_missing", version="1.0",
        steps=[_ok_step("only_step")],
    )
    runner = WorkflowRunner(defn)
    with pytest.raises(SableError):
        runner.resume("nonexistent_run_id_xyz", conn=wf_db)


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


def test_skip_if_false_executes_step(wf_db):
    """skip_if returning False must NOT skip the step."""
    defn = WorkflowDefinition(
        name="test_skip_false", version="1.0",
        steps=[
            StepDefinition(
                name="step_a",
                fn=lambda ctx: StepResult("completed", {"ran": True}),
                max_retries=0,
                skip_if=lambda ctx: False,
            ),
        ],
    )
    runner = WorkflowRunner(defn)
    run_id = runner.run("wf_org", {}, conn=wf_db)

    steps = get_workflow_steps(wf_db, run_id)
    assert steps[0]["status"] == "completed"


# ---------------------------------------------------------------------------
# P1-1: redact_error — secrets not persisted to DB
# ---------------------------------------------------------------------------

def test_redact_error_in_step_failure(wf_db):
    """Step error containing an API key must be redacted before writing to DB."""
    def leaky_step(ctx):
        raise ValueError("call failed with sk-ant-FAKEKEYABCDEFGHIJKLMNOP123456789 in response")

    defn = WorkflowDefinition(
        name="test_redact", version="1.0",
        steps=[StepDefinition(name="leaky", fn=leaky_step, max_retries=0)],
    )
    runner = WorkflowRunner(defn)
    with pytest.raises(SableError):
        runner.run("wf_org", {}, conn=wf_db)

    step = wf_db.execute(
        "SELECT error FROM workflow_steps WHERE step_name='leaky'"
    ).fetchone()
    assert step is not None
    assert "sk-ant-" not in (step["error"] or "")
    assert "[REDACTED]" in (step["error"] or "")


# ---------------------------------------------------------------------------
# P1-2: _skip_reason key — does not pollute accumulated context
# ---------------------------------------------------------------------------

def test_skip_reason_does_not_overwrite_prior_output(wf_db):
    """skip_if output must not clobber a legitimate 'reason' key from a prior step."""
    defn = WorkflowDefinition(
        name="test_skip_reason", version="1.0",
        steps=[
            _ok_step("step_a", output={"reason": "community gap"}),
            StepDefinition(
                name="step_b",
                fn=lambda ctx: StepResult("completed", {}),
                max_retries=0,
                skip_if=lambda ctx: True,
            ),
        ],
    )
    runner = WorkflowRunner(defn)
    run_id = runner.run("wf_org", {}, conn=wf_db)

    # After skip, the step_b output_json should use _skip_reason, not "reason"
    step_b = wf_db.execute(
        "SELECT output_json FROM workflow_steps WHERE step_name='step_b'"
    ).fetchone()
    import json
    output = json.loads(step_b["output_json"])
    assert "_skip_reason" in output
    assert "reason" not in output


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


# ---------------------------------------------------------------------------
# Workflow config versioning
# ---------------------------------------------------------------------------

def _failing_defn(name: str, steps: list) -> WorkflowDefinition:
    return WorkflowDefinition(name=name, version="1.0", steps=steps)


def test_version_mismatch_raises(wf_db):
    """Resume raises SableError when step fingerprint changed since run was created."""
    # Run a workflow with step_a + step_b (step_b fails)
    step_b_fail = {"fail": True}

    def step_b_fn(ctx):
        if step_b_fail["fail"]:
            raise ValueError("intentional failure")
        return StepResult("completed", {})

    defn_original = WorkflowDefinition(
        name="version_test", version="1.0",
        steps=[
            _ok_step("step_a"),
            StepDefinition(name="step_b", fn=step_b_fn, max_retries=0),
        ],
    )
    runner = WorkflowRunner(defn_original)
    with pytest.raises(SableError):
        runner.run("wf_org", {}, conn=wf_db)

    run_row = wf_db.execute("SELECT run_id FROM workflow_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    run_id = run_row["run_id"]

    # Resume with a different definition (different step names)
    defn_changed = WorkflowDefinition(
        name="version_test", version="1.0",
        steps=[
            _ok_step("step_a"),
            _ok_step("step_b_renamed"),  # name changed
        ],
    )
    runner2 = WorkflowRunner(defn_changed)
    with pytest.raises(SableError) as exc_info:
        runner2.resume(run_id, conn=wf_db)
    assert exc_info.value.code == STEP_EXECUTION_ERROR
    assert "changed" in str(exc_info.value)


def test_null_version_skips_check(wf_db):
    """Resume succeeds without fingerprint check if stored step_fingerprint is NULL (old run)."""
    # Insert a run directly with NULL step_fingerprint
    import uuid as _uuid
    run_id = _uuid.uuid4().hex
    wf_db.execute(
        """
        INSERT INTO workflow_runs (run_id, org_id, workflow_name, workflow_version, status, config_json, step_fingerprint)
        VALUES (?, 'wf_org', 'null_fp_test', '1.0', 'failed', '{}', NULL)
        """,
        (run_id,),
    )
    wf_db.commit()

    # Resume with any definition — fingerprint check must be skipped
    defn = WorkflowDefinition(
        name="null_fp_test", version="1.0",
        steps=[_ok_step("any_step")],
    )
    runner = WorkflowRunner(defn)
    runner.resume(run_id, conn=wf_db)

    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"


def test_matching_version_resumes(wf_db):
    """Resume succeeds when fingerprint matches (same step names)."""
    step_b_fail = {"fail": True}

    def step_b_fn(ctx):
        if step_b_fail["fail"]:
            raise ValueError("intentional failure")
        return StepResult("completed", {})

    defn = WorkflowDefinition(
        name="match_test", version="1.0",
        steps=[
            _ok_step("step_a"),
            StepDefinition(name="step_b", fn=step_b_fn, max_retries=0),
        ],
    )
    runner = WorkflowRunner(defn)
    with pytest.raises(SableError):
        runner.run("wf_org", {}, conn=wf_db)

    run_row = wf_db.execute("SELECT run_id FROM workflow_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    run_id = run_row["run_id"]

    # Resume with same definition — should succeed
    step_b_fail["fail"] = False
    runner2 = WorkflowRunner(defn)
    runner2.resume(run_id, conn=wf_db)

    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"


def test_legacy_fingerprint_still_resumes(wf_db):
    """Old unversioned fingerprints remain resumable after the v2 fingerprint change."""
    import uuid as _uuid

    run_id = _uuid.uuid4().hex
    wf_db.execute(
        """
        INSERT INTO workflow_runs
            (run_id, org_id, workflow_name, workflow_version, status, config_json, step_fingerprint)
        VALUES (?, 'wf_org', 'legacy_fp_test', '1.0', 'failed', '{}', ?)
        """,
        (run_id, _legacy_workflow_fingerprint(WorkflowDefinition(
            name="legacy_fp_test",
            version="1.0",
            steps=[_ok_step("step_a"), _ok_step("step_b")],
        ))),
    )
    wf_db.execute(
        """
        INSERT INTO workflow_steps
            (step_id, run_id, step_name, step_index, status, input_json, output_json)
        VALUES (?, ?, 'step_a', 0, 'completed', '{}', '{}')
        """,
        (_uuid.uuid4().hex, run_id),
    )
    wf_db.commit()

    defn = WorkflowDefinition(
        name="legacy_fp_test",
        version="1.0",
        steps=[_ok_step("step_a"), _ok_step("step_b")],
    )
    runner = WorkflowRunner(defn)
    runner.resume(run_id, conn=wf_db)

    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"


def test_step_order_change_raises_version_mismatch(wf_db):
    """Reordering steps must trip the version check because resume semantics depend on order."""
    step_b_fail = {"fail": True}

    def step_b_fn(ctx):
        if step_b_fail["fail"]:
            raise ValueError("intentional failure")
        return StepResult("completed", {})

    defn_original = WorkflowDefinition(
        name="order_test", version="1.0",
        steps=[
            _ok_step("step_a"),
            StepDefinition(name="step_b", fn=step_b_fn, max_retries=0),
        ],
    )
    runner = WorkflowRunner(defn_original)
    with pytest.raises(SableError):
        runner.run("wf_org", {}, conn=wf_db)

    run_row = wf_db.execute("SELECT run_id FROM workflow_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    run_id = run_row["run_id"]

    step_b_fail["fail"] = False
    defn_reordered = WorkflowDefinition(
        name="order_test", version="1.0",
        steps=[
            StepDefinition(name="step_b", fn=step_b_fn, max_retries=0),
            _ok_step("step_a"),
        ],
    )
    runner2 = WorkflowRunner(defn_reordered)
    with pytest.raises(SableError) as exc_info:
        runner2.resume(run_id, conn=wf_db)

    assert exc_info.value.code == STEP_EXECUTION_ERROR
    assert "changed" in str(exc_info.value)


def test_ignore_version_check_bypasses_error(wf_db):
    """ignore_version_check=True allows resume even when fingerprint mismatches."""
    step_b_fail = {"fail": True}

    def step_b_fn(ctx):
        if step_b_fail["fail"]:
            raise ValueError("intentional failure")
        return StepResult("completed", {})

    defn_original = WorkflowDefinition(
        name="ignore_test", version="1.0",
        steps=[
            _ok_step("step_a"),
            StepDefinition(name="step_b", fn=step_b_fn, max_retries=0),
        ],
    )
    runner = WorkflowRunner(defn_original)
    with pytest.raises(SableError):
        runner.run("wf_org", {}, conn=wf_db)

    run_row = wf_db.execute("SELECT run_id FROM workflow_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    run_id = run_row["run_id"]

    # Resume with DIFFERENT definition but ignore_version_check=True
    step_b_fail["fail"] = False
    defn_changed = WorkflowDefinition(
        name="ignore_test", version="1.0",
        steps=[
            _ok_step("step_a"),
            StepDefinition(name="step_b", fn=step_b_fn, max_retries=0),
            _ok_step("step_c_new"),
        ],
    )
    runner2 = WorkflowRunner(defn_changed)
    runner2.resume(run_id, conn=wf_db, ignore_version_check=True)

    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"


def test_returned_failed_step_marks_step_failed_and_reruns_on_resume(wf_db):
    """StepResult(status='failed') must persist as failed so resume retries the same step."""
    call_log: list[str] = []
    should_fail = {"value": True}

    def step_a(ctx):
        call_log.append("step_a")
        return StepResult("completed", {})

    def step_b(ctx):
        call_log.append("step_b")
        if should_fail["value"]:
            return StepResult("failed", {"partial": True}, error="adapter output invalid")
        return StepResult("completed", {"recovered": True})

    defn = WorkflowDefinition(
        name="returned_failed_resume",
        version="1.0",
        steps=[
            StepDefinition(name="step_a", fn=step_a, max_retries=0),
            StepDefinition(name="step_b", fn=step_b, max_retries=0),
        ],
    )
    runner = WorkflowRunner(defn)

    with pytest.raises(SableError):
        runner.run("wf_org", {}, conn=wf_db)

    run_row = wf_db.execute("SELECT run_id FROM workflow_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    run_id = run_row["run_id"]

    failed_step = wf_db.execute(
        """
        SELECT status, error FROM workflow_steps
        WHERE run_id=? AND step_name='step_b'
        ORDER BY rowid DESC LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    assert failed_step["status"] == "failed"
    assert "adapter output invalid" in failed_step["error"]

    should_fail["value"] = False
    call_log.clear()
    runner.resume(run_id, conn=wf_db)

    assert call_log == ["step_b"]


# ---------------------------------------------------------------------------
# cancel_workflow_run (Slice 3)
# ---------------------------------------------------------------------------

def test_cancel_pending_run(wf_db):
    """Cancelling a pending run sets status to 'cancelled'."""
    from sable_platform.db.workflow_store import cancel_workflow_run, create_workflow_run

    run_id = create_workflow_run(wf_db, "wf_org", "cancel_test", "1.0", {})
    cancel_workflow_run(wf_db, run_id)

    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "cancelled"


def test_cancel_completed_run_raises(wf_db):
    """Cancelling an already-completed run raises SableError."""
    from sable_platform.db.workflow_store import cancel_workflow_run
    from sable_platform.errors import SableError

    defn = WorkflowDefinition(
        name="cancel_completed", version="1.0",
        steps=[_ok_step("only_step")],
    )
    runner = WorkflowRunner(defn)
    run_id = runner.run("wf_org", {}, conn=wf_db)

    with pytest.raises(SableError) as exc_info:
        cancel_workflow_run(wf_db, run_id)
    assert "already completed" in str(exc_info.value)


def test_resume_cancelled_run_raises(wf_db):
    """Resuming a cancelled run raises SableError."""
    from sable_platform.db.workflow_store import cancel_workflow_run, create_workflow_run

    run_id = create_workflow_run(wf_db, "wf_org", "resume_cancel_test", "1.0", {})
    cancel_workflow_run(wf_db, run_id)

    defn = WorkflowDefinition(
        name="resume_cancel_test", version="1.0",
        steps=[_ok_step("only_step")],
    )
    runner = WorkflowRunner(defn)
    with pytest.raises(SableError) as exc_info:
        runner.resume(run_id, conn=wf_db)
    assert "cancelled" in str(exc_info.value)


# ---------------------------------------------------------------------------
# retry_delay_seconds (Slice 4)
# ---------------------------------------------------------------------------

def test_retry_delay_is_applied(wf_db):
    """time.sleep is called with retry_delay_seconds between retry attempts."""
    from unittest.mock import patch

    def always_fails(ctx):
        raise ValueError("always fails")

    defn = WorkflowDefinition(
        name="retry_delay_test", version="1.0",
        steps=[StepDefinition(name="fail_step", fn=always_fails, max_retries=1, retry_delay_seconds=0.1)],
    )
    runner = WorkflowRunner(defn)

    with patch("sable_platform.workflows.engine.time.sleep") as mock_sleep:
        with pytest.raises(SableError):
            runner.run("wf_org", {}, conn=wf_db)

    mock_sleep.assert_called_once_with(0.1)
