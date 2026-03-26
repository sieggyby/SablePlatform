"""Tests for workflow GC (mark_timed_out_runs)."""
from __future__ import annotations

import pytest

from sable_platform.db.workflow_store import mark_timed_out_runs


def test_gc_marks_stuck_run_timed_out(wf_db):
    """Run stuck in 'running' for >6h should be marked timed_out."""
    wf_db.execute(
        """
        INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, started_at)
        VALUES ('stuck_run', 'wf_org', 'test', 'running', datetime('now', '-8 hours'))
        """
    )
    wf_db.commit()

    run_ids = mark_timed_out_runs(wf_db, hours=6)
    assert "stuck_run" in run_ids

    row = wf_db.execute("SELECT status FROM workflow_runs WHERE run_id='stuck_run'").fetchone()
    assert row["status"] == "timed_out"


def test_gc_ignores_recent_run(wf_db):
    """Run started 1h ago should not be marked timed_out with hours=6."""
    wf_db.execute(
        """
        INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, started_at)
        VALUES ('recent_run', 'wf_org', 'test', 'running', datetime('now', '-1 hours'))
        """
    )
    wf_db.commit()

    run_ids = mark_timed_out_runs(wf_db, hours=6)
    assert "recent_run" not in run_ids

    row = wf_db.execute("SELECT status FROM workflow_runs WHERE run_id='recent_run'").fetchone()
    assert row["status"] == "running"


def test_gc_is_idempotent(wf_db):
    """Second call on already timed_out run returns empty list."""
    wf_db.execute(
        """
        INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, started_at)
        VALUES ('stuck2', 'wf_org', 'test', 'running', datetime('now', '-8 hours'))
        """
    )
    wf_db.commit()

    first = mark_timed_out_runs(wf_db, hours=6)
    assert "stuck2" in first

    second = mark_timed_out_runs(wf_db, hours=6)
    assert len(second) == 0
