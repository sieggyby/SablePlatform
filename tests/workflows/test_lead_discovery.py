"""Tests for lead_discovery builtin workflow."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sable_platform.workflows.engine import WorkflowRunner
from sable_platform.workflows.builtins.lead_discovery import LEAD_DISCOVERY
from sable_platform.errors import SableError


def test_lead_discovery_happy_path(tmp_path, monkeypatch, wf_db):
    """Workflow completes and creates entities when Lead Identifier returns pursue leads."""
    # Set up repo dir and output file
    repo_dir = tmp_path / "lead_identifier_repo"
    output_dir = repo_dir / "output"
    output_dir.mkdir(parents=True)
    leads_fixture = {
        "leads": [
            {
                "rank": 1,
                "project": {
                    "project_id": "proj_abc",
                    "name": "Test Project",
                    "twitter_handle": "testproj",
                },
                "scores": {"composite": 0.85, "recommended_action": "pursue"},
                "flags": [],
            }
        ]
    }
    (output_dir / "sable_leads_latest.json").write_text(json.dumps(leads_fixture))

    monkeypatch.setenv("SABLE_LEAD_IDENTIFIER_PATH", str(repo_dir))

    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("", "")
    mock_proc.returncode = 0
    mock_proc.pid = 12345

    with patch("subprocess.Popen", return_value=mock_proc):
        runner = WorkflowRunner(LEAD_DISCOVERY)
        run_id = runner.run("wf_org", {}, conn=wf_db)

    run = wf_db.execute("SELECT status FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
    assert run["status"] == "completed"

    entities = wf_db.execute(
        "SELECT COUNT(*) FROM entities WHERE org_id='wf_org'"
    ).fetchone()[0]
    assert entities >= 1


def test_lead_discovery_fails_without_env(monkeypatch, wf_db):
    """Workflow fails at validate_env when SABLE_LEAD_IDENTIFIER_PATH is not set."""
    monkeypatch.delenv("SABLE_LEAD_IDENTIFIER_PATH", raising=False)

    runner = WorkflowRunner(LEAD_DISCOVERY)
    with pytest.raises(SableError):
        runner.run("wf_org", {}, conn=wf_db)


def test_lead_discovery_unknown_org(tmp_path, monkeypatch, wf_db):
    """Workflow fails at validate_env when org does not exist in DB."""
    repo_dir = tmp_path / "lead_identifier_repo"
    repo_dir.mkdir()
    monkeypatch.setenv("SABLE_LEAD_IDENTIFIER_PATH", str(repo_dir))

    runner = WorkflowRunner(LEAD_DISCOVERY)
    with pytest.raises(SableError):
        runner.run("nonexistent_org_xyz", {}, conn=wf_db)
