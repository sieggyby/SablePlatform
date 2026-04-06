"""Tests for sable_platform.db.workflow_store module."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from sable_platform.db.workflow_store import (
    cancel_workflow_run,
    complete_workflow_run,
    complete_workflow_step,
    create_workflow_run,
    create_workflow_step,
    emit_workflow_event,
    fail_workflow_run,
    fail_workflow_step,
    get_latest_run,
    get_workflow_events,
    get_workflow_run,
    get_workflow_steps,
    mark_timed_out_runs,
    reset_workflow_step_for_retry,
    skip_workflow_step,
    start_workflow_run,
    start_workflow_step,
)
from sable_platform.errors import STEP_EXECUTION_ERROR, WORKFLOW_NOT_FOUND, SableError


def _make_run(conn, org_id, name="test_workflow", config=None, fingerprint=None):
    return create_workflow_run(conn, org_id, name, "1.0", config or {}, fingerprint)


def _make_step(conn, run_id, name="step_1", index=0, input_data=None):
    return create_workflow_step(conn, run_id, name, index, input_data or {})


# ---------------------------------------------------------------------------
# create_workflow_run
# ---------------------------------------------------------------------------

class TestCreateWorkflowRun:
    def test_create(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        assert isinstance(rid, str) and len(rid) == 32

    def test_create_defaults(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        row = get_workflow_run(conn, rid)
        assert row["status"] == "pending"
        assert row["workflow_version"] == "1.0"
        assert json.loads(row["config_json"]) == {}

    def test_create_with_fingerprint(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id, fingerprint="abc123")
        row = get_workflow_run(conn, rid)
        assert row["step_fingerprint"] == "abc123"

    def test_create_with_config(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id, config={"key": "val"})
        row = get_workflow_run(conn, rid)
        assert json.loads(row["config_json"]) == {"key": "val"}

    def test_committed(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        row = conn.execute("SELECT * FROM workflow_runs WHERE run_id=?", (rid,)).fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# Run state transitions
# ---------------------------------------------------------------------------

class TestRunTransitions:
    def test_start(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        start_workflow_run(conn, rid)
        row = get_workflow_run(conn, rid)
        assert row["status"] == "running"
        assert row["started_at"] is not None

    def test_complete(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        start_workflow_run(conn, rid)
        complete_workflow_run(conn, rid)
        row = get_workflow_run(conn, rid)
        assert row["status"] == "completed"
        assert row["completed_at"] is not None

    def test_fail(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        start_workflow_run(conn, rid)
        fail_workflow_run(conn, rid, "something broke")
        row = get_workflow_run(conn, rid)
        assert row["status"] == "failed"
        assert row["error"] == "something broke"
        assert row["completed_at"] is not None

    def test_fail_redacts_secrets(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        fail_workflow_run(conn, rid, "API key sk-ant-XXXXXXXXXXXXXXXXXXXXXXXXXX leaked")
        row = get_workflow_run(conn, rid)
        assert "sk-ant-" not in row["error"]
        assert "[REDACTED]" in row["error"]


# ---------------------------------------------------------------------------
# Step lifecycle
# ---------------------------------------------------------------------------

class TestStepLifecycle:
    def test_create_step(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        sid = _make_step(conn, rid, "extract")
        assert isinstance(sid, str) and len(sid) == 32

    def test_step_defaults(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        sid = _make_step(conn, rid, "extract", index=3, input_data={"x": 1})
        steps = get_workflow_steps(conn, rid)
        assert len(steps) == 1
        assert steps[0]["status"] == "pending"
        assert steps[0]["step_index"] == 3
        assert json.loads(steps[0]["input_json"]) == {"x": 1}

    def test_start_step(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        sid = _make_step(conn, rid)
        start_workflow_step(conn, sid)
        steps = get_workflow_steps(conn, rid)
        assert steps[0]["status"] == "running"
        assert steps[0]["started_at"] is not None

    def test_complete_step(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        sid = _make_step(conn, rid)
        start_workflow_step(conn, sid)
        complete_workflow_step(conn, sid, {"result": "ok"})
        steps = get_workflow_steps(conn, rid)
        assert steps[0]["status"] == "completed"
        assert json.loads(steps[0]["output_json"]) == {"result": "ok"}

    def test_skip_step(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        sid = _make_step(conn, rid)
        skip_workflow_step(conn, sid, "condition not met")
        steps = get_workflow_steps(conn, rid)
        assert steps[0]["status"] == "skipped"
        output = json.loads(steps[0]["output_json"])
        assert output["_skip_reason"] == "condition not met"

    def test_fail_step(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        sid = _make_step(conn, rid)
        start_workflow_step(conn, sid)
        fail_workflow_step(conn, sid, "timeout")
        steps = get_workflow_steps(conn, rid)
        assert steps[0]["status"] == "failed"
        assert steps[0]["error"] == "timeout"
        assert steps[0]["retries"] == 1

    def test_fail_step_increments_retries(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        sid = _make_step(conn, rid)
        fail_workflow_step(conn, sid, "err1")
        fail_workflow_step(conn, sid, "err2")
        steps = get_workflow_steps(conn, rid)
        assert steps[0]["retries"] == 2

    def test_fail_step_redacts_secrets(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        sid = _make_step(conn, rid)
        fail_workflow_step(conn, sid, "Bearer eyJhbGciOi.very.long.token.here")
        steps = get_workflow_steps(conn, rid)
        assert "Bearer [REDACTED]" in steps[0]["error"]

    def test_reset_for_retry(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        sid = _make_step(conn, rid)
        start_workflow_step(conn, sid)
        fail_workflow_step(conn, sid, "oops")
        reset_workflow_step_for_retry(conn, sid)
        steps = get_workflow_steps(conn, rid)
        assert steps[0]["status"] == "pending"
        assert steps[0]["started_at"] is None
        assert steps[0]["completed_at"] is None
        assert steps[0]["error"] is None

    def test_steps_ordered_by_index(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        _make_step(conn, rid, "step_b", index=2)
        _make_step(conn, rid, "step_a", index=1)
        steps = get_workflow_steps(conn, rid)
        assert steps[0]["step_name"] == "step_a"
        assert steps[1]["step_name"] == "step_b"


# ---------------------------------------------------------------------------
# mark_timed_out_runs
# ---------------------------------------------------------------------------

class TestMarkTimedOutRuns:
    def test_marks_stuck_runs(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        start_workflow_run(conn, rid)
        # Backdate started_at to 7 hours ago
        conn.execute(
            "UPDATE workflow_runs SET started_at=datetime('now', '-7 hours') WHERE run_id=?",
            (rid,),
        )
        conn.commit()
        timed_out = mark_timed_out_runs(conn, hours=6)
        assert rid in timed_out
        row = get_workflow_run(conn, rid)
        assert row["status"] == "timed_out"

    def test_does_not_touch_recent_runs(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        start_workflow_run(conn, rid)
        timed_out = mark_timed_out_runs(conn, hours=6)
        assert timed_out == []
        assert get_workflow_run(conn, rid)["status"] == "running"

    def test_does_not_touch_completed_runs(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        start_workflow_run(conn, rid)
        complete_workflow_run(conn, rid)
        conn.execute(
            "UPDATE workflow_runs SET started_at=datetime('now', '-7 hours') WHERE run_id=?",
            (rid,),
        )
        conn.commit()
        timed_out = mark_timed_out_runs(conn, hours=6)
        assert timed_out == []

    def test_custom_hours(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        start_workflow_run(conn, rid)
        conn.execute(
            "UPDATE workflow_runs SET started_at=datetime('now', '-13 hours') WHERE run_id=?",
            (rid,),
        )
        conn.commit()
        assert mark_timed_out_runs(conn, hours=12) == [rid]


# ---------------------------------------------------------------------------
# cancel_workflow_run
# ---------------------------------------------------------------------------

class TestCancelWorkflowRun:
    def test_cancel_pending(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        cancel_workflow_run(conn, rid)
        assert get_workflow_run(conn, rid)["status"] == "cancelled"

    def test_cancel_running(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        start_workflow_run(conn, rid)
        cancel_workflow_run(conn, rid)
        assert get_workflow_run(conn, rid)["status"] == "cancelled"

    def test_cancel_completed_raises(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        complete_workflow_run(conn, rid)
        with pytest.raises(SableError) as exc_info:
            cancel_workflow_run(conn, rid)
        assert exc_info.value.code == STEP_EXECUTION_ERROR

    def test_cancel_timed_out_raises(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        conn.execute("UPDATE workflow_runs SET status='timed_out' WHERE run_id=?", (rid,))
        conn.commit()
        with pytest.raises(SableError) as exc_info:
            cancel_workflow_run(conn, rid)
        assert exc_info.value.code == STEP_EXECUTION_ERROR

    def test_cancel_nonexistent_raises(self, org_db):
        conn, _ = org_db
        with pytest.raises(SableError) as exc_info:
            cancel_workflow_run(conn, "ghost")
        assert exc_info.value.code == WORKFLOW_NOT_FOUND

    def test_cancel_failed_raises(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        start_workflow_run(conn, rid)
        fail_workflow_run(conn, rid, "something broke")
        with pytest.raises(SableError) as exc_info:
            cancel_workflow_run(conn, rid)
        assert exc_info.value.code == STEP_EXECUTION_ERROR

    def test_cancel_already_cancelled_raises(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        cancel_workflow_run(conn, rid)
        with pytest.raises(SableError):
            cancel_workflow_run(conn, rid)


# ---------------------------------------------------------------------------
# emit_workflow_event
# ---------------------------------------------------------------------------

class TestEmitWorkflowEvent:
    def test_emit_basic(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        emit_workflow_event(conn, rid, "step_completed")
        events = get_workflow_events(conn, rid)
        assert len(events) == 1
        assert events[0]["event_type"] == "step_completed"

    def test_emit_with_payload(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        emit_workflow_event(conn, rid, "custom", payload={"key": "val"})
        events = get_workflow_events(conn, rid)
        assert json.loads(events[0]["payload_json"]) == {"key": "val"}

    def test_emit_with_step_id(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        sid = _make_step(conn, rid)
        emit_workflow_event(conn, rid, "step_started", step_id=sid)
        events = get_workflow_events(conn, rid)
        assert events[0]["step_id"] == sid

    def test_emit_webhook_failure_does_not_raise(self, org_db):
        """Webhook dispatch failure is silently caught."""
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        with patch("sable_platform.webhooks.dispatch.dispatch_event", side_effect=Exception("boom")):
            # Should not raise even though dispatch fails
            emit_workflow_event(conn, rid, "test")
        events = get_workflow_events(conn, rid)
        assert len(events) == 1


# ---------------------------------------------------------------------------
# get_workflow_run / get_workflow_steps / get_workflow_events
# ---------------------------------------------------------------------------

class TestGetters:
    def test_get_run_none(self, org_db):
        conn, _ = org_db
        assert get_workflow_run(conn, "ghost") is None

    def test_get_steps_empty(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        assert get_workflow_steps(conn, rid) == []

    def test_get_events_empty(self, org_db):
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        assert get_workflow_events(conn, rid) == []


# ---------------------------------------------------------------------------
# get_latest_run
# ---------------------------------------------------------------------------

class TestGetLatestRun:
    def test_returns_latest(self, org_db):
        conn, org_id = org_db
        rid1 = _make_run(conn, org_id, name="wf")
        complete_workflow_run(conn, rid1)
        rid2 = _make_run(conn, org_id, name="wf")
        # Force ordering
        conn.execute(
            "UPDATE workflow_runs SET created_at=datetime('now', '+1 second') WHERE run_id=?",
            (rid2,),
        )
        conn.commit()
        row = get_latest_run(conn, org_id, "wf")
        assert row["run_id"] == rid2

    def test_filter_by_status(self, org_db):
        conn, org_id = org_db
        rid1 = _make_run(conn, org_id, name="wf")
        complete_workflow_run(conn, rid1)
        rid2 = _make_run(conn, org_id, name="wf")
        row = get_latest_run(conn, org_id, "wf", status="completed")
        assert row["run_id"] == rid1

    def test_returns_none_when_empty(self, org_db):
        conn, org_id = org_db
        assert get_latest_run(conn, org_id, "nonexistent") is None

    def test_complete_without_start_sets_completed_at_only(self, org_db):
        """Document: completing a pending run works but started_at stays NULL."""
        conn, org_id = org_db
        rid = _make_run(conn, org_id)
        complete_workflow_run(conn, rid)
        row = get_workflow_run(conn, rid)
        assert row["status"] == "completed"
        assert row["started_at"] is None
        assert row["completed_at"] is not None

    def test_scoped_to_org(self, org_db):
        conn, org_id = org_db
        conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('other', 'Other')")
        conn.commit()
        _make_run(conn, org_id, name="wf")
        assert get_latest_run(conn, "other", "wf") is None
