"""Tests for CultGraderAdapter."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from sable_platform.adapters.cult_grader import CultGraderAdapter
from sable_platform.contracts.leads import ProspectHandoff
from sable_platform.errors import SableError, INVALID_CONFIG


def _handoff(prospect_yaml_path: str) -> ProspectHandoff:
    return ProspectHandoff(
        org_id="test_org",
        prospect_yaml_path=prospect_yaml_path,
        project_name="TestProj",
    )


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------

def test_status_pending_dir_not_exists(tmp_path):
    adapter = CultGraderAdapter()
    non_existent = str(tmp_path / "ghost_dir")
    assert adapter.status(non_existent) == "pending"


def test_status_running_dir_exists_no_meta(tmp_path):
    checkpoint = tmp_path / "run_dir"
    checkpoint.mkdir()
    adapter = CultGraderAdapter()
    assert adapter.status(str(checkpoint)) == "running"


def test_status_completed_meta_present(tmp_path):
    checkpoint = tmp_path / "run_dir"
    checkpoint.mkdir()
    (checkpoint / "run_meta.json").write_text(json.dumps({"run_id": "r1"}))
    adapter = CultGraderAdapter()
    assert adapter.status(str(checkpoint)) == "completed"


# ---------------------------------------------------------------------------
# get_result()
# ---------------------------------------------------------------------------

def test_get_result_empty_on_missing_files(tmp_path):
    checkpoint = tmp_path / "run_dir"
    checkpoint.mkdir()
    adapter = CultGraderAdapter()
    result = adapter.get_result(str(checkpoint))
    assert result == {}


def test_get_result_reads_both_json_files(tmp_path):
    checkpoint = tmp_path / "run_dir"
    checkpoint.mkdir()
    (checkpoint / "diagnostic.json").write_text(json.dumps({"score": 0.9}))
    (checkpoint / "run_meta.json").write_text(json.dumps({"run_id": "r2"}))
    adapter = CultGraderAdapter()
    result = adapter.get_result(str(checkpoint))
    assert result["diagnostic"] == {"score": 0.9}
    assert result["run_meta"] == {"run_id": "r2"}


# ---------------------------------------------------------------------------
# _parse_latest_run()
# ---------------------------------------------------------------------------

def _write_prospect_yaml(path: Path, slug: str) -> None:
    path.write_text(yaml.dump({"project_slug": slug, "name": "Test"}))


def test_parse_latest_run_uses_symlink_path(tmp_path):
    repo = tmp_path / "repo"
    slug = "myslug"
    checkpoint = repo / "diagnostics" / slug / "runs" / "latest"
    checkpoint.mkdir(parents=True)
    run_meta = {"run_id": "run_abc", "fit_score": 0.8, "recommended_action": "pursue"}
    (checkpoint / "run_meta.json").write_text(json.dumps(run_meta))

    prospect_yaml = tmp_path / "prospect.yaml"
    _write_prospect_yaml(prospect_yaml, slug)
    handoff = _handoff(str(prospect_yaml))

    adapter = CultGraderAdapter()
    result = adapter._parse_latest_run(repo, handoff)

    assert result["run_id"] == "run_abc"
    assert result["checkpoint_path"] == str(checkpoint)
    assert result["fit_score"] == pytest.approx(0.8)


def test_parse_latest_run_fallback_to_dated_dir(tmp_path):
    repo = tmp_path / "repo"
    slug = "myslug2"
    dated_dir = repo / "diagnostics" / slug / "runs" / "2026-03-20"
    dated_dir.mkdir(parents=True)
    run_meta = {"run_id": "run_dated", "fit_score": 0.7}
    (dated_dir / "run_meta.json").write_text(json.dumps(run_meta))

    prospect_yaml = tmp_path / "prospect2.yaml"
    _write_prospect_yaml(prospect_yaml, slug)
    handoff = _handoff(str(prospect_yaml))

    adapter = CultGraderAdapter()
    result = adapter._parse_latest_run(repo, handoff)

    assert result["run_id"] == "run_dated"
    assert result["checkpoint_path"] == str(dated_dir)


def test_parse_latest_run_yaml_not_found_raises(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    handoff = _handoff(str(tmp_path / "nonexistent.yaml"))

    adapter = CultGraderAdapter()
    with pytest.raises(SableError) as exc_info:
        adapter._parse_latest_run(repo, handoff)

    assert exc_info.value.code == INVALID_CONFIG
