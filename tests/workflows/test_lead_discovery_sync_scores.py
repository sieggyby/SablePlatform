"""Tests for the _sync_scores step in lead_discovery workflow."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from sable_platform.workflows.engine import WorkflowRunner
from sable_platform.workflows.builtins.lead_discovery import LEAD_DISCOVERY, _sync_scores
from sable_platform.db.workflow_store import get_workflow_steps


def test_sync_scores_step_exists():
    """Verify sync_scores is registered in the workflow definition."""
    step_names = [s.name for s in LEAD_DISCOVERY.steps]
    assert "sync_scores" in step_names
    # Must be after parse_leads and before register_artifacts
    idx_sync = step_names.index("sync_scores")
    idx_parse = step_names.index("parse_leads")
    idx_register = step_names.index("register_artifacts")
    assert idx_parse < idx_sync < idx_register


def test_sync_scores_with_leads(wf_db):
    """_sync_scores maps leads to prospect_scores correctly."""
    ctx = MagicMock()
    ctx.db = wf_db
    ctx.input_data = {
        "leads": [
            {
                "project_id": "test_project_alpha",
                "composite_score": 0.82,
                "tier": "pursue",
                "stage": "enriched",
                "dimensions": {
                    "community_gap": 0.3,
                    "conversation_gap": 0.2,
                    "growth_trajectory": 0.9,
                    "content_quality": 0.7,
                },
                "rationale": {"summary": "Strong project"},
                "enrichment": {"sector": "defi"},
                "next_action": "schedule_call",
            }
        ],
    }

    result = _sync_scores(ctx)
    assert result.status == "completed"
    assert result.output["scores_synced"] == 1

    # Verify data in DB
    row = wf_db.execute(
        "SELECT * FROM prospect_scores WHERE org_id='test_project_alpha'"
    ).fetchone()
    assert row is not None
    assert row["composite_score"] == 0.82
    assert row["tier"] == "pursue"

    # Verify dimension inversion
    dims = json.loads(row["dimensions_json"])
    assert dims["community_health"] == pytest.approx(0.7, abs=0.01)  # 1.0 - 0.3
    assert dims["language_signal"] == pytest.approx(0.8, abs=0.01)  # 1.0 - 0.2
    assert dims["growth_trajectory"] == 0.9  # passthrough
    assert dims["content_quality"] == 0.7    # passthrough


def test_sync_scores_empty_leads(wf_db):
    """_sync_scores returns 0 when no leads are provided."""
    ctx = MagicMock()
    ctx.db = wf_db
    ctx.input_data = {"leads": []}

    result = _sync_scores(ctx)
    assert result.status == "completed"
    assert result.output["scores_synced"] == 0


def test_sync_scores_no_leads_key(wf_db):
    """_sync_scores handles missing leads key gracefully."""
    ctx = MagicMock()
    ctx.db = wf_db
    ctx.input_data = {}

    result = _sync_scores(ctx)
    assert result.status == "completed"
    assert result.output["scores_synced"] == 0


def test_sync_scores_partial_dimensions(wf_db):
    """_sync_scores handles leads with missing dimension keys."""
    ctx = MagicMock()
    ctx.db = wf_db
    ctx.input_data = {
        "leads": [
            {
                "project_id": "partial_dims",
                "composite_score": 0.5,
                "tier": "monitor",
                "dimensions": {"community_gap": 0.4},
            }
        ],
    }

    result = _sync_scores(ctx)
    assert result.output["scores_synced"] == 1

    row = wf_db.execute(
        "SELECT dimensions_json FROM prospect_scores WHERE org_id='partial_dims'"
    ).fetchone()
    dims = json.loads(row["dimensions_json"])
    assert dims == {"community_health": pytest.approx(0.6, abs=0.01)}


def test_sync_scores_duplicate_project_id(wf_db):
    """When two leads share the same project_id, last-write-wins (upsert contract)."""
    ctx = MagicMock()
    ctx.db = wf_db
    ctx.input_data = {
        "leads": [
            {"project_id": "dup_project", "composite_score": 0.5, "tier": "monitor", "dimensions": {}},
            {"project_id": "dup_project", "composite_score": 0.9, "tier": "pursue", "dimensions": {}},
        ],
    }

    result = _sync_scores(ctx)
    assert result.output["scores_synced"] == 2  # both processed

    # Only one row exists (upsert on org_id + run_date)
    rows = wf_db.execute(
        "SELECT * FROM prospect_scores WHERE org_id='dup_project'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["composite_score"] == 0.9  # last write wins
    assert rows[0]["tier"] == "pursue"


def test_sync_scores_max_retries_zero():
    """sync_scores step must be non-fatal (max_retries=0)."""
    for step in LEAD_DISCOVERY.steps:
        if step.name == "sync_scores":
            assert step.max_retries == 0
            return
    pytest.fail("sync_scores step not found in workflow definition")
