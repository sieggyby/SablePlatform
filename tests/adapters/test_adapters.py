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


def test_subprocess_success():
    adapter = _TestAdapter()
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "ok"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        result = adapter._run_subprocess(["echo", "hi"], cwd=Path("/tmp"))
    assert result.returncode == 0
    mock_run.assert_called_once()


def test_subprocess_nonzero_raises_sable_error():
    adapter = _TestAdapter()
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "something went wrong"

    with patch("subprocess.run", return_value=mock_result):
        with pytest.raises(SableError) as exc_info:
            adapter._run_subprocess(["false"], cwd=Path("/tmp"))

    assert exc_info.value.code == STEP_EXECUTION_ERROR
    assert "1" in exc_info.value.message or "went wrong" in exc_info.value.message


def test_subprocess_timeout_raises_sable_error():
    adapter = _TestAdapter()

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="sleep", timeout=1)):
        with pytest.raises(SableError) as exc_info:
            adapter._run_subprocess(["sleep", "999"], cwd=Path("/tmp"), timeout=1)

    assert exc_info.value.code == STEP_EXECUTION_ERROR
    assert "timed out" in exc_info.value.message.lower()


def test_subprocess_command_not_found_raises():
    adapter = _TestAdapter()

    with patch("subprocess.run", side_effect=FileNotFoundError("no such file")):
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
    mock_conn.execute.return_value.fetchone.return_value = {"org_id": "test_org", "status": "completed"}
    with patch("sable_platform.adapters.tracking_sync.get_db") as mock_get_db:
        result = adapter.get_result("test_org", conn=mock_conn)
    mock_get_db.assert_not_called()
    assert result == {"org_id": "test_org", "status": "completed"}
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
    mock_conn.execute.return_value.fetchall.return_value = [
        {"artifact_type": "twitter_strategy_brief", "stale": 0}
    ]
    with patch("sable_platform.adapters.slopper.get_db") as mock_get_db:
        result = adapter.get_result("test_org", conn=mock_conn)
    mock_get_db.assert_not_called()
    assert result == {"artifacts": [{"artifact_type": "twitter_strategy_brief", "stale": 0}]}
    mock_conn.close.assert_not_called()
