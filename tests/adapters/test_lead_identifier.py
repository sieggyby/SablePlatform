"""Tests for LeadIdentifierAdapter.get_result() — LI-1 fix."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from sable_platform.adapters.lead_identifier import LeadIdentifierAdapter


def _write_leads_json(tmp_dir: Path, leads_data: list[dict]) -> None:
    """Write a sable_leads_latest.json file in the expected output dir."""
    output_dir = tmp_dir / "output"
    output_dir.mkdir(exist_ok=True)
    payload = {"run_id": "test", "generated_at": "2026-04-04", "leads": leads_data}
    (output_dir / "sable_leads_latest.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _make_ranked_project(
    project_id: str = "alpha",
    name: str = "Alpha Protocol",
    composite: float = 0.80,
    community_gap: float | None = 0.3,
    conversation_gap: float | None = 0.2,
    engagement_gap: float | None = 0.25,
    tge_proximity: float | None = 0.9,
    recommended_action: str | None = None,
    flags: list[str] | None = None,
) -> dict:
    """Build a RankedProject dict matching Lead Identifier JSON shape."""
    scores: dict = {"composite": composite}
    if community_gap is not None:
        scores["community_gap"] = community_gap
    if conversation_gap is not None:
        scores["conversation_gap"] = conversation_gap
    if engagement_gap is not None:
        scores["engagement_gap"] = engagement_gap
    if tge_proximity is not None:
        scores["tge_proximity"] = tge_proximity
    if recommended_action is not None:
        scores["recommended_action"] = recommended_action
    return {
        "rank": 1,
        "project": {"project_id": project_id, "name": name},
        "scores": scores,
        "flags": flags or [],
    }


class TestGetResultWithRecommendedAction:
    """When recommended_action IS present in raw JSON."""

    def test_keeps_pursue_and_monitor(self, tmp_path):
        _write_leads_json(tmp_path, [
            _make_ranked_project("a", composite=0.80, recommended_action="pursue"),
            _make_ranked_project("b", composite=0.60, recommended_action="monitor"),
            _make_ranked_project("c", composite=0.40, recommended_action="pass"),
        ])
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            adapter = LeadIdentifierAdapter()
            result = adapter.get_result("latest")
        leads = result["leads"]
        assert len(leads) == 2
        ids = {l["project_id"] for l in leads}
        assert ids == {"a", "b"}

    def test_unknown_action_treated_as_pass(self, tmp_path):
        _write_leads_json(tmp_path, [
            _make_ranked_project("x", composite=0.90, recommended_action="investigate"),
        ])
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        assert result["leads"] == []


class TestGetResultWithoutRecommendedAction:
    """When recommended_action is ABSENT — derives from composite_score."""

    def test_high_composite_becomes_pursue(self, tmp_path):
        _write_leads_json(tmp_path, [
            _make_ranked_project("high", composite=0.75),  # no recommended_action
        ])
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        assert len(result["leads"]) == 1
        assert result["leads"][0]["recommended_action"] == "pursue"
        assert result["leads"][0]["tier"] == "Tier 1"

    def test_medium_composite_becomes_monitor(self, tmp_path):
        _write_leads_json(tmp_path, [
            _make_ranked_project("mid", composite=0.60),
        ])
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        assert len(result["leads"]) == 1
        assert result["leads"][0]["recommended_action"] == "monitor"
        assert result["leads"][0]["tier"] == "Tier 2"

    def test_low_composite_filtered_out(self, tmp_path):
        _write_leads_json(tmp_path, [
            _make_ranked_project("low", composite=0.40),
        ])
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        assert result["leads"] == []


class TestThresholdBoundaries:
    """Exact boundary values at 0.55 and 0.70."""

    def test_exact_070_is_pursue(self, tmp_path):
        _write_leads_json(tmp_path, [
            _make_ranked_project("boundary", composite=0.70),
        ])
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        assert result["leads"][0]["recommended_action"] == "pursue"
        assert result["leads"][0]["tier"] == "Tier 1"

    def test_exact_055_is_monitor(self, tmp_path):
        _write_leads_json(tmp_path, [
            _make_ranked_project("boundary", composite=0.55),
        ])
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        assert result["leads"][0]["recommended_action"] == "monitor"
        assert result["leads"][0]["tier"] == "Tier 2"

    def test_just_below_055_is_pass(self, tmp_path):
        _write_leads_json(tmp_path, [
            _make_ranked_project("boundary", composite=0.5499),
        ])
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        assert result["leads"] == []


class TestDimensionInversion:
    """Gap → health inversion at the adapter boundary."""

    def test_gap_scores_inverted_correctly(self, tmp_path):
        _write_leads_json(tmp_path, [
            _make_ranked_project(
                "inv", composite=0.80,
                community_gap=0.3, conversation_gap=0.2,
                engagement_gap=0.25, tge_proximity=0.9,
            ),
        ])
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        dims = result["leads"][0]["dimensions"]
        assert dims["community_health"] == pytest.approx(0.7, abs=0.001)
        assert dims["language_signal"] == pytest.approx(0.8, abs=0.001)
        assert dims["engagement_quality"] == pytest.approx(0.75, abs=0.001)
        assert dims["growth_trajectory"] == pytest.approx(0.9, abs=0.001)
        assert dims["sable_fit"] == pytest.approx(0.8, abs=0.001)

    def test_zero_gaps_become_one(self, tmp_path):
        _write_leads_json(tmp_path, [
            _make_ranked_project(
                "zero", composite=0.80,
                community_gap=0.0, conversation_gap=0.0, engagement_gap=0.0,
            ),
        ])
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        dims = result["leads"][0]["dimensions"]
        assert dims["community_health"] == pytest.approx(1.0, abs=0.001)
        assert dims["language_signal"] == pytest.approx(1.0, abs=0.001)
        assert dims["engagement_quality"] == pytest.approx(1.0, abs=0.001)

    def test_missing_gaps_default_to_half(self, tmp_path):
        _write_leads_json(tmp_path, [
            _make_ranked_project(
                "missing", composite=0.80,
                community_gap=None, conversation_gap=None,
                engagement_gap=None, tge_proximity=None,
            ),
        ])
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        dims = result["leads"][0]["dimensions"]
        assert dims["community_health"] == 0.5
        assert dims["language_signal"] == 0.5
        assert dims["engagement_quality"] == 0.5
        assert dims["growth_trajectory"] == 0.5


class TestClamping:
    """Out-of-range gap values are clamped to [0, 1]."""

    def test_gap_above_one_clamped(self, tmp_path):
        _write_leads_json(tmp_path, [
            _make_ranked_project("clamp", composite=0.80, community_gap=1.5),
        ])
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        dims = result["leads"][0]["dimensions"]
        assert dims["community_health"] == 0.0  # clamped, not -0.5

    def test_negative_gap_clamped(self, tmp_path):
        _write_leads_json(tmp_path, [
            _make_ranked_project("clamp", composite=0.80, community_gap=-0.2),
        ])
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        dims = result["leads"][0]["dimensions"]
        assert dims["community_health"] == 1.0  # clamped, not 1.2


class TestEdgeCases:
    def test_zero_composite_with_explicit_pursue(self, tmp_path):
        """composite=0 with explicit pursue is kept but gets Tier 3."""
        _write_leads_json(tmp_path, [
            _make_ranked_project("zero", composite=0.0, recommended_action="pursue"),
        ])
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        lead = result["leads"][0]
        assert lead["tier"] == "Tier 3"
        assert lead["recommended_action"] == "pursue"

    def test_empty_scores_dict(self, tmp_path):
        """Empty scores → composite=0 → pass → filtered out."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        payload = {"leads": [{"project": {"project_id": "x", "name": "X"}, "scores": {}, "flags": []}]}
        (output_dir / "sable_leads_latest.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        assert result["leads"] == []

    def test_missing_project_dict(self, tmp_path):
        """Missing project dict → empty project_id and name."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        payload = {"leads": [{"scores": {"composite": 0.80}, "flags": []}]}
        (output_dir / "sable_leads_latest.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        assert len(result["leads"]) == 1
        assert result["leads"][0]["project_id"] == ""
        assert result["leads"][0]["name"] == ""


class TestEmptyInput:
    def test_empty_leads_list(self, tmp_path):
        _write_leads_json(tmp_path, [])
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        assert result == {"leads": []}

    def test_no_output_file(self, tmp_path):
        # No output dir at all
        with patch.dict(os.environ, {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
            result = LeadIdentifierAdapter().get_result("latest")
        assert result == {"leads": []}
