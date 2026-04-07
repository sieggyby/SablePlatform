"""Tests for trigger_cult_grader_for_tier1 and sync_cult_grader_results steps."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from sable_platform.errors import SableError, BUDGET_EXCEEDED
from sable_platform.workflows.builtins.lead_discovery import (
    LEAD_DISCOVERY,
    _trigger_cult_grader_for_tier1,
    _sync_cult_grader_results,
    _MAX_DIAGNOSTICS_PER_RUN,
)


def _make_ctx(wf_db, leads, org_id="wf_org"):
    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = org_id
    ctx.run_id = "test_run_001"
    ctx.input_data = {"leads": leads}
    ctx.config = {}
    return ctx


def _make_lead(project_id, composite, prospect_yaml_path=""):
    return {
        "project_id": project_id,
        "composite_score": composite,
        "name": project_id,
        "prospect_yaml_path": prospect_yaml_path,
    }


# ---------------------------------------------------------------------------
# Step registration
# ---------------------------------------------------------------------------

class TestStepRegistration:
    def test_trigger_step_in_workflow(self):
        names = [s.name for s in LEAD_DISCOVERY.steps]
        assert "trigger_cult_grader_for_tier1" in names

    def test_sync_results_step_in_workflow(self):
        names = [s.name for s in LEAD_DISCOVERY.steps]
        assert "sync_cult_grader_results" in names

    def test_step_ordering(self):
        names = [s.name for s in LEAD_DISCOVERY.steps]
        idx_sync = names.index("sync_scores")
        idx_trigger = names.index("trigger_cult_grader_for_tier1")
        idx_results = names.index("sync_cult_grader_results")
        idx_artifacts = names.index("register_artifacts")
        assert idx_sync < idx_trigger < idx_results < idx_artifacts

    def test_trigger_step_no_retry(self):
        for step in LEAD_DISCOVERY.steps:
            if step.name == "trigger_cult_grader_for_tier1":
                assert step.max_retries == 0
                return
        pytest.fail("Step not found")


# ---------------------------------------------------------------------------
# Tier 1 filtering
# ---------------------------------------------------------------------------

class TestTier1Filtering:
    def test_only_tier1_triggered(self, wf_db):
        """Only leads with composite >= 0.50 are triggered."""
        leads = [
            _make_lead("high", 0.80),
            _make_lead("mid", 0.50),
            _make_lead("low", 0.40),
        ]
        ctx = _make_ctx(wf_db, leads)

        with patch("sable_platform.adapters.cult_grader.CultGraderAdapter") as MockAdapter, \
             patch("sable_platform.db.cost.check_budget", return_value=(0.0, 5.0)):
            mock_instance = MockAdapter.return_value
            mock_instance.run.return_value = {"status": "submitted", "job_ref": ""}
            result = _trigger_cult_grader_for_tier1(ctx)

        assert result.output["tier1_total"] == 2
        assert result.output["diagnostics_triggered"] == 2

    def test_empty_leads(self, wf_db):
        ctx = _make_ctx(wf_db, [])
        with patch("sable_platform.db.cost.check_budget", return_value=(0.0, 5.0)):
            result = _trigger_cult_grader_for_tier1(ctx)
        assert result.output["tier1_total"] == 0
        assert result.output["diagnostics_triggered"] == 0


# ---------------------------------------------------------------------------
# Bounded trigger count
# ---------------------------------------------------------------------------

class TestBoundedTriggerCount:
    def test_max_10_diagnostics(self, wf_db):
        """No more than _MAX_DIAGNOSTICS_PER_RUN diagnostics triggered."""
        leads = [_make_lead(f"proj_{i}", 0.80) for i in range(15)]
        ctx = _make_ctx(wf_db, leads)

        with patch("sable_platform.adapters.cult_grader.CultGraderAdapter") as MockAdapter, \
             patch("sable_platform.db.cost.check_budget", return_value=(0.0, 5.0)):
            mock_instance = MockAdapter.return_value
            mock_instance.run.return_value = {"status": "submitted", "job_ref": ""}
            result = _trigger_cult_grader_for_tier1(ctx)

        assert result.output["diagnostics_triggered"] == _MAX_DIAGNOSTICS_PER_RUN
        assert result.output["tier1_total"] == 15
        assert "capped" in result.output["bounded_note"]


# ---------------------------------------------------------------------------
# Budget check
# ---------------------------------------------------------------------------

class TestBudgetCheck:
    def test_budget_exceeded_stops_triggers(self, wf_db):
        leads = [_make_lead(f"proj_{i}", 0.80) for i in range(5)]
        ctx = _make_ctx(wf_db, leads)

        call_count = 0

        def budget_side_effect(conn, org_id):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise SableError(BUDGET_EXCEEDED, "Over budget")
            return (0.0, 5.0)

        with patch("sable_platform.adapters.cult_grader.CultGraderAdapter") as MockAdapter, \
             patch("sable_platform.db.cost.check_budget", side_effect=budget_side_effect):
            mock_instance = MockAdapter.return_value
            mock_instance.run.return_value = {"status": "submitted", "job_ref": ""}
            result = _trigger_cult_grader_for_tier1(ctx)

        assert result.status == "completed"
        assert result.output["diagnostics_triggered"] == 2
        assert result.output["diagnostics_skipped_budget"] > 0


# ---------------------------------------------------------------------------
# Partial failure handling
# ---------------------------------------------------------------------------

class TestPartialFailure:
    def test_individual_failure_does_not_fail_step(self, wf_db):
        leads = [_make_lead("ok_proj", 0.80), _make_lead("bad_proj", 0.80)]
        ctx = _make_ctx(wf_db, leads)

        call_count = 0

        def run_side_effect(input_data):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Cult Grader crashed")
            return {"status": "submitted", "job_ref": ""}

        with patch("sable_platform.adapters.cult_grader.CultGraderAdapter") as MockAdapter, \
             patch("sable_platform.db.cost.check_budget", return_value=(0.0, 5.0)):
            mock_instance = MockAdapter.return_value
            mock_instance.run.side_effect = run_side_effect
            result = _trigger_cult_grader_for_tier1(ctx)

        assert result.status == "completed"
        assert result.output["diagnostics_triggered"] == 1
        assert result.output["diagnostics_failed"] == 1
        assert len(result.output["errors"]) == 1

    def test_all_diagnostics_fail(self, wf_db):
        leads = [_make_lead(f"p{i}", 0.80) for i in range(3)]
        ctx = _make_ctx(wf_db, leads)

        with patch("sable_platform.adapters.cult_grader.CultGraderAdapter") as MockAdapter, \
             patch("sable_platform.db.cost.check_budget", return_value=(0.0, 5.0)):
            mock_instance = MockAdapter.return_value
            mock_instance.run.side_effect = RuntimeError("fail")
            result = _trigger_cult_grader_for_tier1(ctx)

        assert result.status == "completed"
        assert result.output["diagnostics_triggered"] == 0
        assert result.output["diagnostics_failed"] == 3


# ---------------------------------------------------------------------------
# sync_cult_grader_results
# ---------------------------------------------------------------------------

class TestSyncCultGraderResults:
    def test_returns_spend(self, wf_db):
        ctx = _make_ctx(wf_db, [])
        ctx.input_data = {"diagnostics_triggered": 3}

        with patch("sable_platform.db.cost.get_weekly_spend", return_value=1.25):
            result = _sync_cult_grader_results(ctx)

        assert result.status == "completed"
        assert result.output["weekly_spend_usd"] == 1.25
        assert result.output["diagnostics_triggered"] == 3
