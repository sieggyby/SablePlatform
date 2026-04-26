"""Tests for SableClientCommsAdapter (V1 stub)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sable_platform.adapters.client_comms import SableClientCommsAdapter
from sable_platform.errors import SableError, INVALID_CONFIG


def test_status_always_completed():
    adapter = SableClientCommsAdapter()
    assert adapter.status("anything") == "completed"


def test_get_result_returns_noop():
    adapter = SableClientCommsAdapter()
    out = adapter.get_result("ref")
    assert out == {"job_ref": "ref", "noop": True}


def test_repo_path_unset_raises():
    adapter = SableClientCommsAdapter()
    with patch.dict("os.environ", {}, clear=True), pytest.raises(SableError) as exc:
        adapter._repo_path()
    assert exc.value.code == INVALID_CONFIG


def test_run_uses_console_script_when_present():
    adapter = SableClientCommsAdapter()
    fake_stdout = json.dumps({"status": "ok", "tool": "sable-comms", "noop": True})
    with patch.dict("os.environ", {"SABLE_CLIENT_COMMS_PATH": "/fake/path"}), \
         patch("pathlib.Path.is_dir", return_value=True), \
         patch("pathlib.Path.exists", return_value=True), \
         patch.object(adapter, "_run_subprocess") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0, stdout=fake_stdout, stderr="")
        result = adapter.run({"argv": ["send", "--org", "tig"]})

    cmd = mock_sub.call_args[0][0]
    assert cmd[0].endswith(".venv/bin/sable-comms")
    assert cmd[1:] == ["send", "--org", "tig"]
    assert result["status"] == "completed"
    assert result["payload"]["noop"] is True
    assert result["payload"]["tool"] == "sable-comms"


def test_run_falls_back_to_module_form_without_venv():
    adapter = SableClientCommsAdapter()
    fake_stdout = json.dumps({"status": "ok", "noop": True})
    with patch.dict("os.environ", {"SABLE_CLIENT_COMMS_PATH": "/fake/path"}), \
         patch("pathlib.Path.is_dir", return_value=True), \
         patch("pathlib.Path.exists", return_value=False), \
         patch.object(adapter, "_run_subprocess") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0, stdout=fake_stdout, stderr="")
        adapter.run({"argv": ["noop"]})

    cmd = mock_sub.call_args[0][0]
    assert cmd == [sys.executable, "-m", "sable_client_comms.cli", "noop"]


def test_run_synthesizes_argv_from_kwargs():
    adapter = SableClientCommsAdapter()
    fake_stdout = json.dumps({"status": "ok", "noop": True})
    with patch.dict("os.environ", {"SABLE_CLIENT_COMMS_PATH": "/fake/path"}), \
         patch("pathlib.Path.is_dir", return_value=True), \
         patch("pathlib.Path.exists", return_value=False), \
         patch.object(adapter, "_run_subprocess") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0, stdout=fake_stdout, stderr="")
        adapter.run({"org": "tig", "channel": "tg"})

    cmd = mock_sub.call_args[0][0]
    assert "noop" in cmd
    assert "--org" in cmd and "tig" in cmd
    assert "--channel" in cmd and "tg" in cmd


def test_run_handles_non_json_stdout_gracefully():
    adapter = SableClientCommsAdapter()
    with patch.dict("os.environ", {"SABLE_CLIENT_COMMS_PATH": "/fake/path"}), \
         patch("pathlib.Path.is_dir", return_value=True), \
         patch("pathlib.Path.exists", return_value=False), \
         patch.object(adapter, "_run_subprocess") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0, stdout="not json output\n", stderr="")
        result = adapter.run({"argv": ["noop"]})
    assert result["status"] == "completed"
    assert result["payload"]["raw_stdout"].startswith("not json")
