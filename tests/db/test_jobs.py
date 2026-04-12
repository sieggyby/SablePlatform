"""Tests for sable_platform.db.jobs module."""
from __future__ import annotations

import json

import pytest

from sable_platform.db.jobs import (
    add_step,
    complete_step,
    create_job,
    fail_step,
    get_job,
    get_resumable_steps,
    resume_job,
    start_step,
)
from sable_platform.errors import MAX_RETRIES_EXCEEDED, SableError


# ---------------------------------------------------------------------------
# create_job
# ---------------------------------------------------------------------------

class TestCreateJob:
    def test_create_minimal(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "diagnostic")
        assert isinstance(jid, str) and len(jid) == 32

    def test_create_with_config(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "sync", config={"key": "value"})
        row = get_job(conn, jid)
        assert json.loads(row["config_json"]) == {"key": "value"}

    def test_create_defaults(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "diagnostic")
        row = get_job(conn, jid)
        assert row["status"] == "pending"
        assert json.loads(row["config_json"]) == {}

    def test_create_committed(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (jid,)).fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# add_step
# ---------------------------------------------------------------------------

class TestAddStep:
    def test_add_basic(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        sid = add_step(conn, jid, "step_1")
        assert isinstance(sid, int)
        row = conn.execute("SELECT step_id FROM job_steps WHERE step_id=?", (sid,)).fetchone()
        assert row["step_id"] == sid

    def test_add_with_order_and_input(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        sid = add_step(conn, jid, "step_1", step_order=5, input_data={"x": 1})
        row = conn.execute("SELECT * FROM job_steps WHERE step_id=?", (sid,)).fetchone()
        assert row["step_order"] == 5
        assert json.loads(row["input_json"]) == {"x": 1}
        assert row["status"] == "pending"


# ---------------------------------------------------------------------------
# start_step / complete_step / fail_step
# ---------------------------------------------------------------------------

class TestStepTransitions:
    def test_start_step(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        sid = add_step(conn, jid, "step_1")
        start_step(conn, sid)
        row = conn.execute("SELECT * FROM job_steps WHERE step_id=?", (sid,)).fetchone()
        assert row["status"] == "running"
        assert row["started_at"] is not None

    def test_complete_step(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        sid = add_step(conn, jid, "step_1")
        start_step(conn, sid)
        complete_step(conn, sid, output={"result": "ok"})
        row = conn.execute("SELECT * FROM job_steps WHERE step_id=?", (sid,)).fetchone()
        assert row["status"] == "completed"
        assert row["completed_at"] is not None
        assert json.loads(row["output_json"]) == {"result": "ok"}

    def test_complete_step_default_output(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        sid = add_step(conn, jid, "step_1")
        complete_step(conn, sid)
        row = conn.execute("SELECT output_json FROM job_steps WHERE step_id=?", (sid,)).fetchone()
        assert json.loads(row["output_json"]) == {}

    def test_fail_step(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        sid = add_step(conn, jid, "step_1")
        start_step(conn, sid)
        fail_step(conn, sid, error="timeout")
        row = conn.execute("SELECT * FROM job_steps WHERE step_id=?", (sid,)).fetchone()
        assert row["status"] == "failed"
        assert row["error"] == "timeout"
        assert row["retries"] == 1

    def test_fail_step_increments_retries(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        sid = add_step(conn, jid, "step_1")
        fail_step(conn, sid, error="err1")
        fail_step(conn, sid, error="err2")
        row = conn.execute("SELECT retries FROM job_steps WHERE step_id=?", (sid,)).fetchone()
        assert row["retries"] == 2


# ---------------------------------------------------------------------------
# get_job
# ---------------------------------------------------------------------------

class TestGetJob:
    def test_get_returns_row(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "diagnostic")
        row = get_job(conn, jid)
        assert row["job_id"] == jid
        assert row["job_type"] == "diagnostic"

    def test_get_nonexistent_returns_none(self, org_db):
        conn, _ = org_db
        assert get_job(conn, "ghost") is None


# ---------------------------------------------------------------------------
# get_resumable_steps
# ---------------------------------------------------------------------------

class TestGetResumableSteps:
    def test_ordered_by_step_order(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        add_step(conn, jid, "b_step", step_order=2)
        add_step(conn, jid, "a_step", step_order=1)
        steps = get_resumable_steps(conn, jid)
        assert steps[0]["step_name"] == "a_step"
        assert steps[1]["step_name"] == "b_step"

    def test_empty(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        assert get_resumable_steps(conn, jid) == []


# ---------------------------------------------------------------------------
# resume_job
# ---------------------------------------------------------------------------

class TestResumeJob:
    def test_skip_completed(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        sid = add_step(conn, jid, "done_step")
        complete_step(conn, sid)
        actions = resume_job(conn, jid)
        assert actions == [{"step_name": "done_step", "action": "skip"}]

    def test_retry_failed_within_limit(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        sid = add_step(conn, jid, "fail_step")
        fail_step(conn, sid, error="oops")  # retries=1
        actions = resume_job(conn, jid, max_retries=2)
        assert actions == [{"step_name": "fail_step", "action": "retry"}]
        row = conn.execute("SELECT status FROM job_steps WHERE step_id=?", (sid,)).fetchone()
        assert row["status"] == "pending"

    def test_retry_exceeds_limit_raises(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        sid = add_step(conn, jid, "fail_step")
        fail_step(conn, sid)
        fail_step(conn, sid)  # retries=2
        with pytest.raises(SableError) as exc_info:
            resume_job(conn, jid, max_retries=2)
        assert exc_info.value.code == MAX_RETRIES_EXCEEDED

    def test_wait_awaiting_input(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        sid = add_step(conn, jid, "wait_step")
        conn.execute("UPDATE job_steps SET status='awaiting_input' WHERE step_id=?", (sid,))
        conn.commit()
        actions = resume_job(conn, jid)
        assert actions == [{"step_name": "wait_step", "action": "wait"}]

    def test_run_pending(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        sid = add_step(conn, jid, "pending_step")
        actions = resume_job(conn, jid)
        assert actions == [{"step_name": "pending_step", "action": "run"}]
        row = conn.execute("SELECT status FROM job_steps WHERE step_id=?", (sid,)).fetchone()
        assert row["status"] == "running"

    def test_run_already_running_step(self, org_db):
        """A running step (crashed mid-exec) gets re-set to running with action 'run'."""
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        sid = add_step(conn, jid, "running_step")
        start_step(conn, sid)
        actions = resume_job(conn, jid)
        assert actions == [{"step_name": "running_step", "action": "run"}]
        row = conn.execute("SELECT status FROM job_steps WHERE step_id=?", (sid,)).fetchone()
        assert row["status"] == "running"

    def test_mixed_steps(self, org_db):
        conn, org_id = org_db
        jid = create_job(conn, org_id, "test")
        s1 = add_step(conn, jid, "step_1", step_order=1)
        s2 = add_step(conn, jid, "step_2", step_order=2)
        complete_step(conn, s1)
        actions = resume_job(conn, jid)
        assert len(actions) == 2
        assert actions[0] == {"step_name": "step_1", "action": "skip"}
        assert actions[1] == {"step_name": "step_2", "action": "run"}
