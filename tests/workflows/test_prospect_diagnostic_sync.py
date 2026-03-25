"""Integration tests for Workflow 1: prospect_diagnostic_sync."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from sable_platform.workflows.engine import WorkflowRunner
from sable_platform.workflows.builtins.prospect_diagnostic_sync import PROSPECT_DIAGNOSTIC_SYNC
from sable_platform.db.workflow_store import get_workflow_run, get_workflow_steps


@pytest.fixture
def prospect_yaml(tmp_path) -> Path:
    """Create a minimal prospect YAML file."""
    p = tmp_path / "test_prospect.yaml"
    p.write_text(
        "name: TestProject\nproject_slug: testproject\ntwitter_handle: testproject\nsable_org: wf_org\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def mock_cult_grader_adapter(wf_db):
    """Mock CultGraderAdapter to succeed immediately without subprocess."""

    def mock_run(self, input_data):
        # Simulate writing a completed diagnostic_runs row
        cult_run_id = "mock_cult_run_001"
        wf_db.execute(
            """
            INSERT INTO diagnostic_runs
                (org_id, run_type, status, completed_at, cult_run_id, project_slug,
                 overall_grade, fit_score, recommended_action, sable_verdict)
            VALUES (?, 'cult_doctor', 'completed', datetime('now'), ?, 'testproject', 'B+', 72, 'investigate', 'promising')
            """,
            ("wf_org", cult_run_id),
        )
        wf_db.commit()
        return {
            "status": "submitted",
            "job_ref": "/tmp/fake_checkpoint",
            "checkpoint_path": "/tmp/fake_checkpoint",
            "fit_score": 72,
            "recommended_action": "investigate",
        }

    def mock_status(self, job_ref):
        return "completed"

    def mock_get_result(self, job_ref):
        return {
            "run_meta": {
                "run_id": "mock_cult_run_001",
                "overall_grade": "B+",
                "fit_score": 72,
                "sable_verdict": "promising",
            }
        }

    with patch("sable_platform.adapters.cult_grader.CultGraderAdapter.run", mock_run), \
         patch("sable_platform.adapters.cult_grader.CultGraderAdapter.status", mock_status), \
         patch("sable_platform.adapters.cult_grader.CultGraderAdapter.get_result", mock_get_result):
        yield


def test_workflow1_happy_path(wf_db, prospect_yaml, mock_cult_grader_adapter):
    """Full happy path: all 6 steps complete, run status = completed."""
    runner = WorkflowRunner(PROSPECT_DIAGNOSTIC_SYNC)
    config = {
        "prospect_yaml_path": str(prospect_yaml),
        "org_id": "wf_org",
    }
    run_id = runner.run("wf_org", config, conn=wf_db)

    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed", f"Run failed: {run['error']}"

    steps = get_workflow_steps(wf_db, run_id)
    step_names = {s["step_name"]: s["status"] for s in steps}

    assert step_names["validate_prospect"] == "completed"
    assert step_names["request_diagnostic"] == "completed"
    assert step_names["poll_diagnostic"] == "completed"
    assert step_names["verify_entity_sync"] == "completed"
    assert step_names["register_artifacts"] == "completed"
    assert step_names["mark_complete"] == "completed"


def test_workflow1_validate_missing_yaml(wf_db):
    """validate_prospect fails if YAML path does not exist."""
    runner = WorkflowRunner(PROSPECT_DIAGNOSTIC_SYNC)
    config = {
        "prospect_yaml_path": "/nonexistent/path/config.yaml",
        "org_id": "wf_org",
    }
    from sable_platform.errors import SableError
    with pytest.raises(SableError):
        runner.run("wf_org", config, conn=wf_db)


def test_workflow1_validate_unknown_org(tmp_path):
    """validate_prospect fails if org_id does not exist in DB."""
    import sqlite3
    from sable_platform.db.connection import ensure_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    # No org inserted

    p = tmp_path / "c.yaml"
    p.write_text("name: X\nproject_slug: x\n", encoding="utf-8")

    runner = WorkflowRunner(PROSPECT_DIAGNOSTIC_SYNC)
    from sable_platform.errors import SableError
    with pytest.raises(SableError):
        runner.run("nonexistent_org", {"prospect_yaml_path": str(p), "org_id": "nonexistent_org"}, conn=conn)
