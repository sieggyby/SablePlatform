"""Contract tests for adapter CLI interfaces and Pydantic model round-trips.

These tests verify:
1. All adapters satisfy the AdapterBase protocol (name, run, status, get_result)
2. Each adapter's subprocess command uses the expected CLI interface
3. Pydantic contract models round-trip serialize/deserialize with realistic data
4. Adapter result parsers handle expected output formats
"""
from __future__ import annotations

import json
import sys
from unittest.mock import patch, MagicMock

import pytest

from sable_platform.adapters.base import SubprocessAdapterMixin
from sable_platform.adapters.lead_identifier import LeadIdentifierAdapter
from sable_platform.adapters.cult_grader import CultGraderAdapter
from sable_platform.adapters.tracking_sync import SableTrackingAdapter
from sable_platform.adapters.slopper import SlopperAdvisoryAdapter
from sable_platform.contracts.leads import Lead, DimensionScores, ProspectHandoff
from sable_platform.contracts.tracking import TrackingMetadata
from sable_platform.contracts.entities import Entity, EntityHandle, EntityTag


# ---------------------------------------------------------------------------
# Protocol compliance: every adapter has name, run, status, get_result
# ---------------------------------------------------------------------------

_ADAPTER_CLASSES = [
    LeadIdentifierAdapter,
    CultGraderAdapter,
    SableTrackingAdapter,
    SlopperAdvisoryAdapter,
]


@pytest.mark.parametrize("cls", _ADAPTER_CLASSES, ids=lambda c: c.__name__)
def test_adapter_has_required_attributes(cls):
    """Every adapter exposes name, run, status, get_result."""
    adapter = cls()
    assert isinstance(adapter.name, str)
    assert len(adapter.name) > 0
    assert callable(adapter.run)
    assert callable(adapter.status)
    assert callable(adapter.get_result)


@pytest.mark.parametrize("cls", _ADAPTER_CLASSES, ids=lambda c: c.__name__)
def test_adapter_is_subprocess_mixin(cls):
    """Every adapter uses SubprocessAdapterMixin."""
    assert issubclass(cls, SubprocessAdapterMixin)


# ---------------------------------------------------------------------------
# CLI command shape verification
# ---------------------------------------------------------------------------

def test_lead_identifier_command_shape():
    """LeadIdentifierAdapter constructs 'python main.py run [--pass1-only]'."""
    adapter = LeadIdentifierAdapter()
    with patch.dict("os.environ", {"SABLE_LEAD_IDENTIFIER_PATH": "/fake/path"}), \
         patch("pathlib.Path.is_dir", return_value=True), \
         patch.object(adapter, "_run_subprocess") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0)
        adapter.run({"pass1_only": True})
        cmd = mock_sub.call_args[0][0]
        assert cmd[0] == sys.executable
        assert cmd[1] == "main.py"
        assert cmd[2] == "run"
        assert "--pass1-only" in cmd


def test_lead_identifier_command_no_pass1():
    """Without pass1_only, --pass1-only flag is absent."""
    adapter = LeadIdentifierAdapter()
    with patch.dict("os.environ", {"SABLE_LEAD_IDENTIFIER_PATH": "/fake/path"}), \
         patch("pathlib.Path.is_dir", return_value=True), \
         patch.object(adapter, "_run_subprocess") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0)
        adapter.run({"pass1_only": False})
        cmd = mock_sub.call_args[0][0]
        assert "--pass1-only" not in cmd


def test_cult_grader_command_shape():
    """CultGraderAdapter constructs 'python diagnose.py --config <path>'."""
    adapter = CultGraderAdapter()
    with patch.dict("os.environ", {"SABLE_CULT_GRADER_PATH": "/fake/path"}), \
         patch("pathlib.Path.is_dir", return_value=True), \
         patch("pathlib.Path.exists", return_value=False), \
         patch.object(adapter, "_run_subprocess") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0)
        try:
            adapter.run({"prospect_yaml_path": "/fake/prospect.yaml", "org_id": "test"})
        except Exception:
            pass  # _parse_latest_run may fail, but we captured the subprocess call
        assert mock_sub.called, "CultGraderAdapter.run() never called _run_subprocess"
        cmd = mock_sub.call_args[0][0]
        assert cmd[0] == sys.executable
        assert cmd[1] == "diagnose.py"
        assert "--config" in cmd


def test_tracking_sync_command_shape():
    """SableTrackingAdapter constructs 'python -m app.platform_sync_runner <org_id>'."""
    adapter = SableTrackingAdapter()
    with patch.dict("os.environ", {"SABLE_TRACKING_PATH": "/fake/path"}), \
         patch("pathlib.Path.is_dir", return_value=True), \
         patch.object(adapter, "_run_subprocess") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0)
        adapter.run({"org_id": "test_org"})
        cmd = mock_sub.call_args[0][0]
        assert cmd == [sys.executable, "-m", "app.platform_sync_runner", "test_org"]


def test_slopper_command_shape():
    """SlopperAdvisoryAdapter constructs 'python -m sable advise <@handle>'."""
    adapter = SlopperAdvisoryAdapter()
    with patch.dict("os.environ", {"SABLE_SLOPPER_PATH": "/fake/path"}), \
         patch("pathlib.Path.is_dir", return_value=True), \
         patch.object(adapter, "_resolve_primary_handle", return_value="@test_handle"), \
         patch.object(adapter, "_run_subprocess") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0)
        adapter.run({"org_id": "test_org"})
        cmd = mock_sub.call_args[0][0]
        assert cmd == [sys.executable, "-m", "sable", "advise", "@test_handle"]


# ---------------------------------------------------------------------------
# Pydantic contract round-trip serialization
# ---------------------------------------------------------------------------

def test_lead_round_trip():
    """Lead model serializes and deserializes with realistic data."""
    lead = Lead(
        project_id="aethir",
        name="Aethir",
        twitter_handle="@aaborondia",
        composite_score=0.75,
        recommended_action="pursue",
        tier="Tier 1",
        stage="lead",
        dimensions=DimensionScores(
            community_health=0.8,
            language_signal=0.6,
            growth_trajectory=0.7,
            engagement_quality=0.9,
            sable_fit=0.75,
        ),
        rationale={"key_strength": "strong community"},
        flags=["high_tge_proximity"],
    )
    dumped = lead.model_dump()
    restored = Lead.model_validate(dumped)
    assert restored.project_id == "aethir"
    assert restored.dimensions.community_health == 0.8
    assert restored.tier == "Tier 1"

    # JSON round-trip
    json_str = lead.model_dump_json()
    from_json = Lead.model_validate_json(json_str)
    assert from_json.composite_score == 0.75


def test_dimension_scores_defaults():
    """DimensionScores with no args uses 0.5 defaults."""
    dims = DimensionScores()
    assert dims.community_health == 0.5
    assert dims.sable_fit == 0.5


def test_prospect_handoff_round_trip():
    """ProspectHandoff serializes with realistic data."""
    handoff = ProspectHandoff(
        org_id="test_org",
        prospect_yaml_path="/path/to/prospect.yaml",
    )
    restored = ProspectHandoff.model_validate(handoff.model_dump())
    assert restored.org_id == "test_org"


def test_tracking_metadata_round_trip():
    """TrackingMetadata round-trips with all 17 fields."""
    meta = TrackingMetadata(
        url="https://twitter.com/user/status/123",
        canonical_author_handle="@user",
        quality_score=0.85,
        engagement_score=0.7,
        lexicon_adoption=0.3,
        emotional_valence="positive",
        format_type="thread",
        intent_type="educational",
        topic_tags=["governance", "tokenomics"],
        is_reusable_template=True,
    )
    dumped = meta.model_dump()
    restored = TrackingMetadata.model_validate(dumped)
    assert restored.schema_version == 1
    assert restored.source_tool == "sable_tracking"
    assert restored.topic_tags == ["governance", "tokenomics"]
    assert restored.is_reusable_template is True

    # JSON round-trip
    json_str = meta.model_dump_json()
    from_json = TrackingMetadata.model_validate_json(json_str)
    assert from_json.quality_score == 0.85


def test_entity_round_trip():
    """Entity model with all status values."""
    for status in ("candidate", "confirmed", "archived"):
        entity = Entity(entity_id="e1", org_id="org1", status=status)
        restored = Entity.model_validate(entity.model_dump())
        assert restored.status == status


def test_entity_handle_round_trip():
    """EntityHandle serializes correctly."""
    handle = EntityHandle(entity_id="e1", platform="twitter", handle="alice")
    restored = EntityHandle.model_validate(handle.model_dump())
    assert restored.platform == "twitter"


def test_entity_tag_round_trip():
    """EntityTag with confidence and expiry."""
    tag = EntityTag(entity_id="e1", tag="cultist", confidence=0.9, expires_at="2026-12-01")
    restored = EntityTag.model_validate(tag.model_dump())
    assert restored.confidence == 0.9
    assert restored.expires_at == "2026-12-01"


# ---------------------------------------------------------------------------
# Adapter result parser format verification
# ---------------------------------------------------------------------------

def test_lead_identifier_get_result_parses_envelope(tmp_path):
    """get_result parses the RankedProject JSON envelope correctly."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    leads_json = {
        "run_id": "test_001",
        "generated_at": "2026-04-04T12:00:00Z",
        "leads": [
            {
                "rank": 1,
                "project": {
                    "project_id": "proj_a",
                    "name": "Project A",
                    "twitter_handle": "@proj_a",
                },
                "scores": {
                    "composite": 0.80,
                    "community_gap": 0.2,
                    "conversation_gap": 0.3,
                    "tge_proximity": 0.7,
                    "engagement_gap": 0.1,
                },
                "flags": ["high_tge_proximity"],
            },
            {
                "rank": 2,
                "project": {"project_id": "proj_b", "name": "Project B"},
                "scores": {"composite": 0.40},  # below monitor threshold → pass → filtered
                "flags": [],
            },
        ],
    }
    (output_dir / "sable_leads_latest.json").write_text(json.dumps(leads_json))

    adapter = LeadIdentifierAdapter()
    with patch.dict("os.environ", {"SABLE_LEAD_IDENTIFIER_PATH": str(tmp_path)}):
        result = adapter.get_result("latest")

    assert len(result["leads"]) == 1  # proj_b filtered as "pass"
    lead = result["leads"][0]
    assert lead["project_id"] == "proj_a"
    assert lead["tier"] == "Tier 1"
    assert lead["dimensions"]["community_health"] == 0.8  # 1.0 - 0.2


def test_cult_grader_get_result_parses_checkpoint(tmp_path):
    """get_result reads diagnostic.json and run_meta.json from checkpoint."""
    (tmp_path / "diagnostic.json").write_text('{"fit_score": 7.5}')
    (tmp_path / "run_meta.json").write_text('{"run_id": "run_001"}')

    adapter = CultGraderAdapter()
    result = adapter.get_result(str(tmp_path))
    assert result["diagnostic"]["fit_score"] == 7.5
    assert result["run_meta"]["run_id"] == "run_001"
