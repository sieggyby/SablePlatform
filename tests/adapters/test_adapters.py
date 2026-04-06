"""Tests for SubprocessAdapterMixin error handling."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from sable_platform.adapters.base import SubprocessAdapterMixin
from sable_platform.adapters.tracking_sync import SableTrackingAdapter
from sable_platform.adapters.slopper import SlopperAdvisoryAdapter
from sable_platform.errors import SableError, STEP_EXECUTION_ERROR


class _TestAdapter(SubprocessAdapterMixin):
    name = "test"

    def run(self, input_data): ...
    def status(self, job_ref): ...
    def get_result(self, job_ref): ...


def _mock_popen(returncode=0, stdout="", stderr=""):
    """Create a MagicMock that behaves like subprocess.Popen."""
    mock = MagicMock()
    mock.communicate.return_value = (stdout, stderr)
    mock.returncode = returncode
    mock.pid = 12345
    mock.wait.return_value = None
    return mock


def test_subprocess_success():
    adapter = _TestAdapter()
    mock_proc = _mock_popen(returncode=0, stdout="ok")

    with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
        result = adapter._run_subprocess(["echo", "hi"], cwd=Path("/tmp"))
    assert result.returncode == 0
    mock_popen.assert_called_once()


def test_subprocess_nonzero_raises_sable_error():
    adapter = _TestAdapter()
    mock_proc = _mock_popen(returncode=1, stderr="something went wrong")

    with patch("subprocess.Popen", return_value=mock_proc):
        with pytest.raises(SableError) as exc_info:
            adapter._run_subprocess(["false"], cwd=Path("/tmp"))

    assert exc_info.value.code == STEP_EXECUTION_ERROR
    assert "1" in exc_info.value.message or "went wrong" in exc_info.value.message


def test_subprocess_timeout_raises_sable_error():
    adapter = _TestAdapter()
    mock_proc = MagicMock()
    mock_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="sleep", timeout=1)
    mock_proc.pid = 12345
    mock_proc.wait.return_value = None

    with patch("subprocess.Popen", return_value=mock_proc), \
         patch("os.killpg"):
        with pytest.raises(SableError) as exc_info:
            adapter._run_subprocess(["sleep", "999"], cwd=Path("/tmp"), timeout=1)

    assert exc_info.value.code == STEP_EXECUTION_ERROR
    assert "timed out" in exc_info.value.message.lower()


def test_subprocess_command_not_found_raises():
    adapter = _TestAdapter()

    with patch("subprocess.Popen", side_effect=FileNotFoundError("no such file")):
        with pytest.raises(SableError) as exc_info:
            adapter._run_subprocess(["nonexistent_command"], cwd=Path("/tmp"))

    assert exc_info.value.code == STEP_EXECUTION_ERROR


def test_tracking_adapter_status_uses_provided_conn():
    adapter = SableTrackingAdapter()
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = {"status": "completed"}
    with patch("sable_platform.adapters.tracking_sync.get_db") as mock_get_db:
        result = adapter.status("test_org", conn=mock_conn)
    mock_get_db.assert_not_called()
    assert result == "completed"
    mock_conn.close.assert_not_called()


def test_tracking_adapter_get_result_uses_provided_conn():
    adapter = SableTrackingAdapter()
    mock_conn = MagicMock()
    mock_row = {"org_id": "test_org", "sync_type": "sable_tracking", "status": "completed"}
    mock_conn.execute.return_value.fetchone.return_value = mock_row
    with patch("sable_platform.adapters.tracking_sync.get_db") as mock_get_db:
        result = adapter.get_result("test_org", conn=mock_conn)
    mock_get_db.assert_not_called()
    assert result == mock_row
    mock_conn.close.assert_not_called()


def test_slopper_adapter_status_uses_provided_conn():
    adapter = SlopperAdvisoryAdapter()
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = {"stale": 0}
    with patch("sable_platform.adapters.slopper.get_db") as mock_get_db:
        result = adapter.status("test_org", conn=mock_conn)
    mock_get_db.assert_not_called()
    assert result == "completed"
    mock_conn.close.assert_not_called()


def test_slopper_adapter_get_result_uses_provided_conn():
    adapter = SlopperAdvisoryAdapter()
    mock_conn = MagicMock()
    mock_row = {"org_id": "test_org", "artifact_type": "twitter_strategy_brief", "stale": 0}
    mock_conn.execute.return_value.fetchall.return_value = [mock_row]
    with patch("sable_platform.adapters.slopper.get_db") as mock_get_db:
        result = adapter.get_result("test_org", conn=mock_conn)
    mock_get_db.assert_not_called()
    assert result == {"artifacts": [mock_row]}
    mock_conn.close.assert_not_called()
