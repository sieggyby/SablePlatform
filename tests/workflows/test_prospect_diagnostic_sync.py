"""Integration tests for Workflow 1: prospect_diagnostic_sync."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from sable_platform.workflows.engine import WorkflowRunner
from sable_platform.workflows.builtins.prospect_diagnostic_sync import (
    PROSPECT_DIAGNOSTIC_SYNC,
    _register_artifacts,
)
from sable_platform.db.workflow_store import get_workflow_run, get_workflow_steps
from sable_platform.workflows.models import StepContext


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


# ---------------------------------------------------------------------------
# DIAG-ORG: sable_org mismatch validation
# ---------------------------------------------------------------------------

def test_sable_org_mismatch_fails_before_diagnostic(wf_db, tmp_path):
    """YAML sable_org that doesn't match ctx.org_id must raise SableError before adapter runs."""
    from sable_platform.errors import SableError

    p = tmp_path / "mismatch.yaml"
    p.write_text(
        "name: MismatchProject\nproject_slug: mismatch\ntwitter_handle: mismatch\nsable_org: wrong_org\n",
        encoding="utf-8",
    )

    adapter_called = []

    def mock_run(self, input_data):
        adapter_called.append(True)
        return {"status": "submitted", "job_ref": "/tmp/x", "checkpoint_path": "/tmp/x"}

    with patch("sable_platform.adapters.cult_grader.CultGraderAdapter.run", mock_run):
        with pytest.raises(SableError) as exc_info:
            WorkflowRunner(PROSPECT_DIAGNOSTIC_SYNC).run(
                "wf_org",
                {"prospect_yaml_path": str(p), "org_id": "wf_org"},
                conn=wf_db,
            )

    assert "sable_org" in exc_info.value.message
    assert "wrong_org" in exc_info.value.message
    assert not adapter_called, "CultGraderAdapter.run must not be called when sable_org mismatches"


def test_sable_org_absent_passes_through(wf_db, tmp_path, mock_cult_grader_adapter):
    """YAML without sable_org field must pass validation and reach the adapter."""
    p = tmp_path / "no_sable_org.yaml"
    p.write_text(
        "name: NoOrgProject\nproject_slug: noorg\ntwitter_handle: noorg\n",
        encoding="utf-8",
    )

    run_id = WorkflowRunner(PROSPECT_DIAGNOSTIC_SYNC).run(
        "wf_org",
        {"prospect_yaml_path": str(p), "org_id": "wf_org"},
        conn=wf_db,
    )

    from sable_platform.db.workflow_store import get_workflow_run
    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed", "Absent sable_org should not block the workflow"


def test_sable_org_matches_passes_through(wf_db, tmp_path, mock_cult_grader_adapter):
    """YAML sable_org matching ctx.org_id must pass validation without error."""
    p = tmp_path / "match.yaml"
    p.write_text(
        "name: MatchProject\nproject_slug: match\ntwitter_handle: match\nsable_org: wf_org\n",
        encoding="utf-8",
    )

    run_id = WorkflowRunner(PROSPECT_DIAGNOSTIC_SYNC).run(
        "wf_org",
        {"prospect_yaml_path": str(p), "org_id": "wf_org"},
        conn=wf_db,
    )

    from sable_platform.db.workflow_store import get_workflow_run
    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"


# ---------------------------------------------------------------------------
# YAML contract normalization: project_name / name / project_slug aliases
# ---------------------------------------------------------------------------

def test_project_name_canonical_field_accepted(wf_db, tmp_path, mock_cult_grader_adapter):
    """YAML using 'project_name' (canonical) must pass validation."""
    p = tmp_path / "canon.yaml"
    p.write_text("project_name: CanonProject\ntwitter_handle: canon\n", encoding="utf-8")

    run_id = WorkflowRunner(PROSPECT_DIAGNOSTIC_SYNC).run(
        "wf_org", {"prospect_yaml_path": str(p), "org_id": "wf_org"}, conn=wf_db,
    )
    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"


def test_project_name_alias_name(wf_db, tmp_path, mock_cult_grader_adapter):
    """YAML using 'name' alias must be accepted and normalized to project_name."""
    p = tmp_path / "alias_name.yaml"
    p.write_text("name: AliasNameProject\ntwitter_handle: aliasname\n", encoding="utf-8")

    run_id = WorkflowRunner(PROSPECT_DIAGNOSTIC_SYNC).run(
        "wf_org", {"prospect_yaml_path": str(p), "org_id": "wf_org"}, conn=wf_db,
    )
    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"


def test_project_name_alias_project_slug(wf_db, tmp_path, mock_cult_grader_adapter):
    """YAML using 'project_slug' alias must be accepted and normalized to project_name."""
    p = tmp_path / "alias_slug.yaml"
    p.write_text("project_slug: alias_slug_proj\ntwitter_handle: aliasslug\n", encoding="utf-8")

    run_id = WorkflowRunner(PROSPECT_DIAGNOSTIC_SYNC).run(
        "wf_org", {"prospect_yaml_path": str(p), "org_id": "wf_org"}, conn=wf_db,
    )
    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"


def test_project_name_missing_all_raises(wf_db, tmp_path):
    """YAML with none of project_name, name, project_slug must raise SableError."""
    from sable_platform.errors import SableError

    p = tmp_path / "no_name.yaml"
    p.write_text("twitter_handle: noname\n", encoding="utf-8")

    with pytest.raises(SableError) as exc_info:
        WorkflowRunner(PROSPECT_DIAGNOSTIC_SYNC).run(
            "wf_org", {"prospect_yaml_path": str(p), "org_id": "wf_org"}, conn=wf_db,
        )
    assert "project_name" in exc_info.value.message


def test_register_artifacts_returns_inserted_ids(wf_db, tmp_path):
    """register_artifacts returns the inserted artifact IDs, not a SQLite-only side channel."""
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    for name in ("report_internal.md", "report_card.md"):
        (checkpoint / name).write_text(f"# {name}\n", encoding="utf-8")

    ctx = StepContext(
        run_id="run_test",
        step_id="step_test",
        org_id="wf_org",
        step_name="register_artifacts",
        step_index=0,
        input_data={"checkpoint_path": str(checkpoint)},
        db=wf_db,
        config={},
    )

    result = _register_artifacts(ctx)

    rows = wf_db.execute(
        "SELECT artifact_id FROM artifacts WHERE org_id=? ORDER BY artifact_id",
        ("wf_org",),
    ).fetchall()
    artifact_ids = [row["artifact_id"] for row in rows]
    assert result.output["artifact_count"] == 2
    assert result.output["artifact_ids"] == artifact_ids
