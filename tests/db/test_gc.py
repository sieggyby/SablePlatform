"""Tests for SP-RETENTION: data retention garbage collection."""
from __future__ import annotations

import pytest

from tests.conftest import make_test_conn
from sable_platform.db.gc import run_gc


@pytest.fixture
def gc_db():
    conn = make_test_conn(with_org="gc_org")
    yield conn
    conn.close()


def test_gc_empty_db(gc_db):
    """GC on empty DB is a no-op, not an error."""
    counts = run_gc(gc_db, retention_days=90)
    assert sum(counts.values()) == 0


def test_gc_purges_old_workflow_events(gc_db):
    """Events for old terminal runs are deleted together with the run."""
    # Old completed run
    gc_db.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, workflow_version, status, completed_at, created_at) "
        "VALUES ('r1', 'gc_org', 'wf', '1.0', 'completed', datetime('now', '-100 days'), datetime('now', '-100 days'))"
    )
    gc_db.execute(
        "INSERT INTO workflow_events (event_id, run_id, event_type, created_at) "
        "VALUES ('e1', 'r1', 'run_completed', datetime('now', '-100 days'))"
    )
    # Recent completed run — should NOT be purged
    gc_db.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, workflow_version, status, completed_at, created_at) "
        "VALUES ('r2', 'gc_org', 'wf', '1.0', 'completed', datetime('now', '-10 days'), datetime('now', '-10 days'))"
    )
    gc_db.execute(
        "INSERT INTO workflow_events (event_id, run_id, event_type, created_at) "
        "VALUES ('e2', 'r2', 'run_completed', datetime('now', '-10 days'))"
    )
    gc_db.commit()

    counts = run_gc(gc_db, retention_days=90)
    assert counts["workflow_events"] == 1  # e1 deleted (old run), e2 kept (recent run)
    assert counts["workflow_runs"] == 1

    remaining = gc_db.execute("SELECT COUNT(*) as c FROM workflow_events").fetchone()["c"]
    assert remaining == 1


def test_gc_purges_terminal_runs_and_steps(gc_db):
    """Terminal workflow runs and their steps are deleted."""
    gc_db.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, workflow_version, status, completed_at, created_at) "
        "VALUES ('old_run', 'gc_org', 'wf', '1.0', 'completed', datetime('now', '-100 days'), datetime('now', '-100 days'))"
    )
    gc_db.execute(
        "INSERT INTO workflow_steps (step_id, run_id, step_name, step_index, status) "
        "VALUES ('s1', 'old_run', 'step1', 0, 'completed')"
    )
    gc_db.commit()

    counts = run_gc(gc_db, retention_days=90)
    assert counts["workflow_runs"] == 1
    assert counts["workflow_steps"] == 1


def test_gc_does_not_purge_audit_log(gc_db):
    """Audit log is NEVER purged regardless of age."""
    gc_db.execute(
        "INSERT INTO audit_log (actor, action, org_id, timestamp) "
        "VALUES ('test', 'test_action', 'gc_org', datetime('now', '-200 days'))"
    )
    gc_db.commit()

    counts = run_gc(gc_db, retention_days=90)
    assert counts["audit_log"] == 0

    remaining = gc_db.execute("SELECT COUNT(*) as c FROM audit_log").fetchone()["c"]
    assert remaining == 1


def test_gc_does_not_purge_running_runs(gc_db):
    """Running workflow runs are never deleted, even if old."""
    gc_db.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, workflow_version, status, started_at, created_at) "
        "VALUES ('running_run', 'gc_org', 'wf', '1.0', 'running', datetime('now', '-100 days'), datetime('now', '-100 days'))"
    )
    gc_db.commit()

    counts = run_gc(gc_db, retention_days=90)
    assert counts["workflow_runs"] == 0

    row = gc_db.execute("SELECT status FROM workflow_runs WHERE run_id='running_run'").fetchone()
    assert row["status"] == "running"
