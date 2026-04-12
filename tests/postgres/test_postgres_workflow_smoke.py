"""Live PostgreSQL smoke tests for key builtin workflows."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from sable_platform.db.workflow_store import get_workflow_run
from sable_platform.workflows.builtins.lead_discovery import LEAD_DISCOVERY
from sable_platform.workflows.builtins.onboard_client import ONBOARD_CLIENT
from sable_platform.workflows.builtins.prospect_diagnostic_sync import PROSPECT_DIAGNOSTIC_SYNC
from sable_platform.workflows.engine import WorkflowRunner


def test_onboard_client_runs_on_live_postgres(postgres_wf_db, monkeypatch):
    """Workflow runner should complete onboarding against PostgreSQL."""
    for var in (
        "SABLE_TRACKING_PATH",
        "SABLE_SLOPPER_PATH",
        "SABLE_CULT_GRADER_PATH",
        "SABLE_LEAD_IDENTIFIER_PATH",
    ):
        monkeypatch.delenv(var, raising=False)

    run_id = WorkflowRunner(ONBOARD_CLIENT).run("wf_org", {}, conn=postgres_wf_db)

    run = get_workflow_run(postgres_wf_db, run_id)
    sync_row = postgres_wf_db.execute(
        "SELECT sync_id, status FROM sync_runs WHERE org_id=? AND sync_type='onboarding'",
        ("wf_org",),
    ).fetchone()

    assert run["status"] == "completed"
    assert sync_row is not None
    assert sync_row["status"] == "pending"


def test_lead_discovery_runs_on_live_postgres(tmp_path, postgres_wf_db, monkeypatch):
    """Lead discovery should complete and register artifacts on PostgreSQL."""
    repo_dir = tmp_path / "lead_identifier_repo"
    output_dir = repo_dir / "output"
    output_dir.mkdir(parents=True)
    (output_dir / "sable_leads_latest.json").write_text(
        json.dumps(
            {
                "leads": [
                    {
                        "rank": 1,
                        "project": {
                            "project_id": "proj_pg",
                            "name": "PG Prospect",
                            "twitter_handle": "pgprospect",
                        },
                        "scores": {"composite": 0.85, "recommended_action": "pursue"},
                        "flags": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SABLE_LEAD_IDENTIFIER_PATH", str(repo_dir))
    monkeypatch.delenv("SABLE_CULT_GRADER_PATH", raising=False)

    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("", "")
    mock_proc.returncode = 0
    mock_proc.pid = 12345

    with patch("subprocess.Popen", return_value=mock_proc):
        run_id = WorkflowRunner(LEAD_DISCOVERY).run("wf_org", {}, conn=postgres_wf_db)

    run = get_workflow_run(postgres_wf_db, run_id)
    artifact_row = postgres_wf_db.execute(
        "SELECT artifact_type, path FROM artifacts WHERE org_id=?",
        ("wf_org",),
    ).fetchone()
    entity_count = postgres_wf_db.execute(
        "SELECT COUNT(*) FROM entities WHERE org_id=?",
        ("wf_org",),
    ).fetchone()[0]

    assert run["status"] == "completed"
    assert entity_count >= 1
    assert artifact_row["artifact_type"] == "lead_identifier_output"
    assert artifact_row["path"].endswith("sable_leads_latest.json")


def test_prospect_diagnostic_sync_runs_on_live_postgres(tmp_path, postgres_wf_db):
    """Prospect diagnostic sync should complete and register artifacts on PostgreSQL."""
    prospect_yaml = tmp_path / "prospect.yaml"
    prospect_yaml.write_text(
        "name: PG Project\nproject_slug: pg-project\ntwitter_handle: pgproject\nsable_org: wf_org\n",
        encoding="utf-8",
    )
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    for name in ("report_internal.md", "report_card.md"):
        (checkpoint / name).write_text(f"# {name}\n", encoding="utf-8")

    def mock_run(self, input_data):
        postgres_wf_db.execute(
            """
            INSERT INTO diagnostic_runs
                (org_id, run_type, status, completed_at, cult_run_id, project_slug,
                 overall_grade, fit_score, recommended_action, sable_verdict)
            VALUES (?, 'cult_doctor', 'completed', CURRENT_TIMESTAMP, ?, 'pg-project',
                    'B+', 72, 'investigate', 'promising')
            """,
            ("wf_org", "pg_cult_run_001"),
        )
        postgres_wf_db.commit()
        return {
            "status": "submitted",
            "job_ref": str(checkpoint),
            "checkpoint_path": str(checkpoint),
            "fit_score": 72,
            "recommended_action": "investigate",
        }

    def mock_status(self, job_ref):
        return "completed"

    def mock_get_result(self, job_ref):
        return {
            "run_meta": {
                "run_id": "pg_cult_run_001",
                "overall_grade": "B+",
                "fit_score": 72,
                "sable_verdict": "promising",
            }
        }

    with patch("sable_platform.adapters.cult_grader.CultGraderAdapter.run", mock_run), \
         patch("sable_platform.adapters.cult_grader.CultGraderAdapter.status", mock_status), \
         patch("sable_platform.adapters.cult_grader.CultGraderAdapter.get_result", mock_get_result):
        run_id = WorkflowRunner(PROSPECT_DIAGNOSTIC_SYNC).run(
            "wf_org",
            {"prospect_yaml_path": str(prospect_yaml), "org_id": "wf_org"},
            conn=postgres_wf_db,
        )

    run = get_workflow_run(postgres_wf_db, run_id)
    artifact_count = postgres_wf_db.execute(
        "SELECT COUNT(*) FROM artifacts WHERE org_id=? AND artifact_type='cult_doctor_report'",
        ("wf_org",),
    ).fetchone()[0]

    assert run["status"] == "completed"
    assert artifact_count == 2
