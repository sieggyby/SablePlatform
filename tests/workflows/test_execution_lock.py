"""Tests for SP-LOCK: workflow execution locking."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_test_conn, make_test_file_db
from sable_platform.db.workflow_store import (
    create_workflow_run,
    fail_workflow_run,
    start_workflow_run,
    complete_workflow_run,
    unlock_workflow_run,
)
from sable_platform.errors import SableError, WORKFLOW_ALREADY_RUNNING
from sable_platform.workflows.engine import WorkflowRunner
from sable_platform.workflows.models import StepDefinition, StepResult, WorkflowDefinition


def _noop_step(ctx) -> StepResult:
    return StepResult("completed", {"done": True})


_TEST_WF = WorkflowDefinition(
    name="test_lock_wf",
    version="1.0",
    steps=[StepDefinition(name="noop", fn=_noop_step, max_retries=0)],
)


@pytest.fixture
def lock_db():
    conn = make_test_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES (?, ?)", ("org_a", "Org A"))
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES (?, ?)", ("org_b", "Org B"))
    conn.commit()
    yield conn
    conn.close()


class TestConcurrentRunBlocking:
    def test_db_level_lock_blocks_second_connection(self, tmp_path):
        """The DB-level active-run invariant blocks duplicate inserts across connections."""
        db_path = str(tmp_path / "lock.db")
        conn1 = make_test_file_db(db_path, with_org="org_a")
        conn2 = make_test_file_db(db_path)
        try:
            create_workflow_run(conn1, "org_a", "test_lock_wf", "1.0", {})

            with pytest.raises(SableError) as exc_info:
                create_workflow_run(conn2, "org_a", "test_lock_wf", "1.0", {})
            assert exc_info.value.code == WORKFLOW_ALREADY_RUNNING
        finally:
            conn1.close()
            conn2.close()

    def test_second_run_blocked_on_same_org(self, lock_db):
        """Two concurrent runs on the same (org, workflow) → second raises."""
        # Create an in-progress run
        run_id = create_workflow_run(lock_db, "org_a", "test_lock_wf", "1.0", {})
        start_workflow_run(lock_db, run_id)

        runner = WorkflowRunner(_TEST_WF)
        with pytest.raises(SableError) as exc_info:
            runner.run("org_a", {}, conn=lock_db)
        assert exc_info.value.code == WORKFLOW_ALREADY_RUNNING

    def test_different_org_not_blocked(self, lock_db):
        """Run on org_a does not block run on org_b."""
        run_id = create_workflow_run(lock_db, "org_a", "test_lock_wf", "1.0", {})
        start_workflow_run(lock_db, run_id)

        runner = WorkflowRunner(_TEST_WF)
        # Should succeed — different org
        new_run_id = runner.run("org_b", {}, conn=lock_db)
        row = lock_db.execute(
            "SELECT status FROM workflow_runs WHERE run_id=?", (new_run_id,)
        ).fetchone()
        assert row["status"] == "completed"

    def test_completed_run_does_not_block(self, lock_db):
        """A completed run does not block a new one."""
        run_id = create_workflow_run(lock_db, "org_a", "test_lock_wf", "1.0", {})
        start_workflow_run(lock_db, run_id)
        complete_workflow_run(lock_db, run_id)

        runner = WorkflowRunner(_TEST_WF)
        new_run_id = runner.run("org_a", {}, conn=lock_db)
        row = lock_db.execute(
            "SELECT status FROM workflow_runs WHERE run_id=?", (new_run_id,)
        ).fetchone()
        assert row["status"] == "completed"

    def test_failed_run_does_not_block(self, lock_db):
        """A failed run does not block a new one."""
        run_id = create_workflow_run(lock_db, "org_a", "test_lock_wf", "1.0", {})
        start_workflow_run(lock_db, run_id)
        fail_workflow_run(lock_db, run_id, "crashed")

        runner = WorkflowRunner(_TEST_WF)
        new_run_id = runner.run("org_a", {}, conn=lock_db)
        row = lock_db.execute(
            "SELECT status FROM workflow_runs WHERE run_id=?", (new_run_id,)
        ).fetchone()
        assert row["status"] == "completed"


class TestStaleLockRecovery:
    def test_stale_run_auto_failed(self, lock_db):
        """An in_progress run older than 4h is auto-failed and new run proceeds."""
        run_id = create_workflow_run(lock_db, "org_a", "test_lock_wf", "1.0", {})
        # Manually set started_at to 5 hours ago
        lock_db.execute(
            "UPDATE workflow_runs SET status='running', started_at=datetime('now', '-5 hours') WHERE run_id=?",
            (run_id,),
        )
        lock_db.commit()

        runner = WorkflowRunner(_TEST_WF)
        new_run_id = runner.run("org_a", {}, conn=lock_db)

        # Old run should be failed
        old_row = lock_db.execute(
            "SELECT status, error FROM workflow_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        assert old_row["status"] == "failed"
        assert "stale lock" in old_row["error"]

        # New run should complete
        new_row = lock_db.execute(
            "SELECT status FROM workflow_runs WHERE run_id=?", (new_run_id,)
        ).fetchone()
        assert new_row["status"] == "completed"


    def test_stale_pending_run_auto_failed(self, lock_db):
        """A pending run (never started) older than 4h is also auto-failed."""
        run_id = create_workflow_run(lock_db, "org_a", "test_lock_wf", "1.0", {})
        # Pending run — started_at is NULL; make created_at 5h ago
        lock_db.execute(
            "UPDATE workflow_runs SET created_at=datetime('now', '-5 hours') WHERE run_id=?",
            (run_id,),
        )
        lock_db.commit()

        runner = WorkflowRunner(_TEST_WF)
        new_run_id = runner.run("org_a", {}, conn=lock_db)

        old_row = lock_db.execute(
            "SELECT status FROM workflow_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        assert old_row["status"] == "failed"

        new_row = lock_db.execute(
            "SELECT status FROM workflow_runs WHERE run_id=?", (new_run_id,)
        ).fetchone()
        assert new_row["status"] == "completed"


class TestResumeRespectLock:
    def test_resume_blocked_by_other_active_run(self, lock_db):
        """Resuming run B while run A is active for same (org, wf) → blocked."""
        # Create run A — active
        run_a = create_workflow_run(lock_db, "org_a", "test_lock_wf", "1.0", {})
        start_workflow_run(lock_db, run_a)

        # Create run B — failed, inserted directly because the active-run lock
        # correctly prevents a second pending/running row for the same key.
        run_b = "failed_run_b"
        lock_db.execute(
            "INSERT INTO workflow_runs"
            " (run_id, org_id, workflow_name, workflow_version, status, config_json, completed_at)"
            " VALUES (?, ?, ?, '1.0', 'failed', '{}', datetime('now'))",
            (run_b, "org_a", "test_lock_wf"),
        )
        lock_db.commit()

        runner = WorkflowRunner(_TEST_WF)
        with pytest.raises(SableError) as exc_info:
            runner.resume(run_b, conn=lock_db)
        assert exc_info.value.code == WORKFLOW_ALREADY_RUNNING


class TestDifferentWorkflowNames:
    def test_same_org_different_workflow_not_blocked(self, lock_db):
        """Different workflows on the same org don't block each other."""
        run_id = create_workflow_run(lock_db, "org_a", "other_workflow", "1.0", {})
        start_workflow_run(lock_db, run_id)

        runner = WorkflowRunner(_TEST_WF)  # test_lock_wf != other_workflow
        new_run_id = runner.run("org_a", {}, conn=lock_db)
        row = lock_db.execute(
            "SELECT status FROM workflow_runs WHERE run_id=?", (new_run_id,)
        ).fetchone()
        assert row["status"] == "completed"


class TestUnlockCommand:
    def test_unlock_transitions_to_failed(self, lock_db):
        run_id = create_workflow_run(lock_db, "org_a", "test_lock_wf", "1.0", {})
        start_workflow_run(lock_db, run_id)

        assert unlock_workflow_run(lock_db, run_id) is True

        row = lock_db.execute(
            "SELECT status, error FROM workflow_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        assert row["status"] == "failed"
        assert "manually unlocked" in row["error"]

    def test_unlock_completed_run_returns_false(self, lock_db):
        run_id = create_workflow_run(lock_db, "org_a", "test_lock_wf", "1.0", {})
        start_workflow_run(lock_db, run_id)
        complete_workflow_run(lock_db, run_id)

        assert unlock_workflow_run(lock_db, run_id) is False
