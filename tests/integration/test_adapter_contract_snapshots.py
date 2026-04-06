"""Snapshot integration tests: parse frozen adapter output through the real adapter
get_result() path (or the canonical Pydantic contract for DB-backed adapters).

These tests guard against contract drift — if a required field is added or renamed
in a Pydantic contract OR in the adapter's normalize logic, the frozen fixture will
fail and signal the break. Fixtures live in tests/integration/fixtures/.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_cult_grader_fixture_parses(tmp_path):
    """CultGrader result fixture must parse through CultGraderAdapter.get_result().

    Exercises the real validation path: run_id required in run_meta, diagnostic must
    be a dict. If the adapter adds new required-field checks, this test catches it.
    """
    from sable_platform.adapters.cult_grader import CultGraderAdapter

    data = _load("cult_grader_result.json")

    # Write fixture files to a temp checkpoint dir (mirrors real CultGrader output layout)
    (tmp_path / "run_meta.json").write_text(json.dumps(data["run_meta"]), encoding="utf-8")
    (tmp_path / "diagnostic.json").write_text(json.dumps(data["diagnostic"]), encoding="utf-8")

    result = CultGraderAdapter().get_result(str(tmp_path))

    assert result["run_meta"]["run_id"], "run_meta.run_id must be non-empty"
    assert isinstance(result["diagnostic"], dict), "diagnostic must be a dict"


def test_lead_identifier_fixture_parses(tmp_path, monkeypatch):
    """LeadIdentifier result fixture must parse through LeadIdentifierAdapter.get_result().

    Exercises the real normalization path: gap inversion, tier derivation, Lead contract
    construction. If the adapter's mapping logic changes, this test catches it.
    """
    from sable_platform.adapters.lead_identifier import LeadIdentifierAdapter

    data = _load("lead_identifier_result.json")

    # Write fixture to the path get_result() resolves: $SABLE_LEAD_IDENTIFIER_PATH/output/sable_leads_latest.json
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "sable_leads_latest.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("SABLE_LEAD_IDENTIFIER_PATH", str(tmp_path))

    result = LeadIdentifierAdapter().get_result("latest")

    leads = result["leads"]
    assert len(leads) > 0, "Fixture must produce at least one non-pass lead"
    for lead in leads:
        assert lead["project_id"], "Each lead must have a project_id"
        assert lead["name"], "Each lead must have a name"
        assert lead["recommended_action"] in ("pursue", "monitor"), (
            "Pass leads must be filtered; only pursue/monitor should appear"
        )
        # Verify dimension scores were produced via the real normalization path
        dims = lead["dimensions"]
        assert 0.0 <= dims["community_health"] <= 1.0
        assert 0.0 <= dims["sable_fit"] <= 1.0


def test_tracking_metadata_fixture_parses():
    """TrackingMetadata fixture must parse through the canonical contract (17 fields)."""
    from sable_platform.contracts.tracking import TrackingMetadata

    data = _load("tracking_result.json")
    meta = TrackingMetadata.model_validate(data)
    assert meta.schema_version == 1
    assert meta.source_tool == "sable_tracking"
    assert isinstance(meta.topic_tags, list)


def test_slopper_fixture_parses():
    """Slopper artifact fixture must parse each row through the Artifact contract."""
    from sable_platform.contracts.artifacts import Artifact

    data = _load("slopper_result.json")
    artifacts = data["artifacts"]
    assert len(artifacts) > 0, "Fixture must have at least one artifact"

    for row in artifacts:
        art = Artifact.model_validate(row)
        assert art.org_id
        assert art.artifact_type
