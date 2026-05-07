"""Tests for SableKOLAdapter — subprocess mocked."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sable_platform.adapters.sablekol import SableKOLAdapter
from sable_platform.errors import SableError


def _mock_popen(returncode=0, stdout="", stderr=""):
    mock = MagicMock()
    mock.communicate.return_value = (stdout, stderr)
    mock.returncode = returncode
    mock.pid = 12345
    mock.wait.return_value = None
    return mock


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """Create a fake SableKOL repo dir with a fake .venv/bin/python."""
    repo = tmp_path / "SableKOL"
    repo.mkdir()
    venv_py = repo / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()
    monkeypatch.setenv("SABLE_KOL_PATH", str(repo))
    return repo


def test_ingest_invokes_cli(fake_repo):
    adapter = SableKOLAdapter()
    with patch("subprocess.Popen", return_value=_mock_popen(stdout="ingest: 100 parsed")) as p:
        result = adapter.ingest(list_export="/tmp/cahit.json")
    assert result["status"] == "completed"
    assert "ingest: 100 parsed" in result["stdout"]
    cmd = p.call_args[0][0]
    assert cmd[1:3] == ["-m", "sable_kol.cli"]
    assert "ingest" in cmd
    assert "--list-export" in cmd


def test_classify_passes_flags(fake_repo):
    adapter = SableKOLAdapter()
    with patch("subprocess.Popen", return_value=_mock_popen(stdout="ok")) as p:
        adapter.classify(limit=50, force=True)
    cmd = p.call_args[0][0]
    assert "--limit" in cmd and "50" in cmd
    assert "--force" in cmd


def test_crossref_invokes_cli(fake_repo):
    adapter = SableKOLAdapter()
    with patch("subprocess.Popen", return_value=_mock_popen(stdout="crossref ok")):
        result = adapter.crossref()
    assert result["status"] == "completed"


def test_find_returns_parsed_json(fake_repo):
    adapter = SableKOLAdapter()
    payload = {
        "project": {"source": "org", "org_id": "tig"},
        "results": [{"handle": "alice", "score": 80}],
        "query_metadata": {"cost_usd": 0.1},
    }
    with patch(
        "subprocess.Popen",
        return_value=_mock_popen(stdout=json.dumps(payload)),
    ):
        out = adapter.find(org_id="tig", limit=10)
    assert out == payload


def test_find_invalid_json_raises(fake_repo):
    adapter = SableKOLAdapter()
    with patch("subprocess.Popen", return_value=_mock_popen(stdout="not json at all")):
        with pytest.raises(RuntimeError):
            adapter.find(org_id="tig")


def test_find_requires_org_or_handle(fake_repo):
    adapter = SableKOLAdapter()
    with pytest.raises(ValueError):
        adapter.find()


def test_find_external_requires_sector(fake_repo):
    adapter = SableKOLAdapter()
    with pytest.raises(ValueError):
        adapter.find(external_handle="newproject")


def test_missing_repo_path_raises_sable_error(monkeypatch):
    monkeypatch.delenv("SABLE_KOL_PATH", raising=False)
    adapter = SableKOLAdapter()
    with pytest.raises(SableError):
        adapter.ingest(list_export="/tmp/x")


def test_uses_repo_venv_python(fake_repo):
    adapter = SableKOLAdapter()
    with patch("subprocess.Popen", return_value=_mock_popen(stdout="ok")) as p:
        adapter.ingest(list_export="/tmp/x")
    cmd = p.call_args[0][0]
    venv_py = fake_repo / ".venv" / "bin" / "python"
    assert cmd[0] == str(venv_py)
