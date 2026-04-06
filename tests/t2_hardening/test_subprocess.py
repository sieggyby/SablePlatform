"""T2-SUBPROCESS: process group isolation for adapter subprocess runner."""
from __future__ import annotations

import inspect
import os
import signal
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from sable_platform.adapters.base import SubprocessAdapterMixin
from sable_platform.errors import SableError, STEP_EXECUTION_ERROR


class _TestAdapter(SubprocessAdapterMixin):
    name = "test"

    def run(self, input_data): ...
    def status(self, job_ref): ...
    def get_result(self, job_ref): ...


def test_subprocess_uses_start_new_session():
    """Popen is called with start_new_session=True for process group isolation."""
    adapter = _TestAdapter()
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("ok", "")
    mock_proc.returncode = 0
    mock_proc.pid = 12345

    with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
        adapter._run_subprocess(["echo", "hi"], cwd=Path("/tmp"))

    _, kwargs = mock_popen.call_args
    assert kwargs["start_new_session"] is True


def test_timeout_calls_killpg():
    """On timeout, os.killpg is called with the child's PID to kill the process group."""
    adapter = _TestAdapter()
    mock_proc = MagicMock()
    mock_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="sleep", timeout=1)
    mock_proc.pid = 99999
    mock_proc.wait.return_value = None

    with patch("subprocess.Popen", return_value=mock_proc), \
         patch("os.killpg") as mock_killpg:
        with pytest.raises(SableError) as exc_info:
            adapter._run_subprocess(["sleep", "999"], cwd=Path("/tmp"), timeout=1)

    assert exc_info.value.code == STEP_EXECUTION_ERROR
    assert "timed out" in exc_info.value.message.lower()
    mock_killpg.assert_called_once_with(99999, signal.SIGKILL)
    mock_proc.wait.assert_called_once()


def test_timeout_killpg_handles_already_exited():
    """If the process already exited, killpg raises ProcessLookupError — handled gracefully."""
    adapter = _TestAdapter()
    mock_proc = MagicMock()
    mock_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="sleep", timeout=1)
    mock_proc.pid = 99999
    mock_proc.wait.return_value = None

    with patch("subprocess.Popen", return_value=mock_proc), \
         patch("os.killpg", side_effect=ProcessLookupError):
        with pytest.raises(SableError):
            adapter._run_subprocess(["sleep", "999"], cwd=Path("/tmp"), timeout=1)
    # Should not re-raise ProcessLookupError


def test_successful_subprocess_returns_completed_process():
    """Normal execution returns CompletedProcess with stdout/stderr."""
    adapter = _TestAdapter()
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("output", "")
    mock_proc.returncode = 0
    mock_proc.pid = 12345

    with patch("subprocess.Popen", return_value=mock_proc):
        result = adapter._run_subprocess(["echo", "hi"], cwd=Path("/tmp"))

    assert isinstance(result, subprocess.CompletedProcess)
    assert result.stdout == "output"
    assert result.returncode == 0


def test_nonzero_exit_raises_sable_error():
    """Non-zero exit code raises SableError with stderr snippet."""
    adapter = _TestAdapter()
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("", "something went wrong")
    mock_proc.returncode = 1
    mock_proc.pid = 12345

    with patch("subprocess.Popen", return_value=mock_proc):
        with pytest.raises(SableError) as exc_info:
            adapter._run_subprocess(["false"], cwd=Path("/tmp"))

    assert exc_info.value.code == STEP_EXECUTION_ERROR


def test_command_not_found_raises_sable_error():
    """FileNotFoundError is translated to SableError."""
    adapter = _TestAdapter()

    with patch("subprocess.Popen", side_effect=FileNotFoundError("no such file")):
        with pytest.raises(SableError) as exc_info:
            adapter._run_subprocess(["nonexistent"], cwd=Path("/tmp"))

    assert exc_info.value.code == STEP_EXECUTION_ERROR
    assert "not found" in exc_info.value.message.lower()


def test_source_has_start_new_session():
    """Source-level check: Popen call includes start_new_session=True."""
    source = inspect.getsource(SubprocessAdapterMixin._run_subprocess)
    assert "start_new_session=True" in source


def test_source_has_killpg():
    """Source-level check: timeout handler uses os.killpg."""
    source = inspect.getsource(SubprocessAdapterMixin._run_subprocess)
    assert "os.killpg" in source
    assert "signal.SIGKILL" in source
