"""Adapter base types and subprocess mixin."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Literal, Protocol

from sable_platform.errors import SableError, STEP_EXECUTION_ERROR

logger = logging.getLogger(__name__)


class AdapterBase(Protocol):
    name: str

    def run(self, input_data: dict) -> dict: ...
    def status(self, job_ref: str) -> Literal["pending", "running", "completed", "failed"]: ...
    def get_result(self, job_ref: str) -> dict: ...


class SubprocessAdapterMixin:
    """Mixin providing a safe subprocess runner for adapter implementations."""

    def _run_subprocess(
        self,
        cmd: list[str],
        cwd: Path,
        timeout: int = 1800,
        env: dict | None = None,
    ) -> subprocess.CompletedProcess:
        """Run a subprocess synchronously. Raises SableError on failure or timeout."""
        logger.debug("Adapter subprocess: %s (cwd=%s)", " ".join(cmd), cwd)
        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise SableError(
                STEP_EXECUTION_ERROR,
                f"Subprocess timed out after {timeout}s: {' '.join(cmd)}",
            ) from exc
        except FileNotFoundError as exc:
            raise SableError(
                STEP_EXECUTION_ERROR,
                f"Command not found: {cmd[0]}",
            ) from exc

        if result.returncode != 0:
            stderr_snippet = (result.stderr or "")[:500]
            logger.debug("Subprocess stderr: %s", result.stderr)
            raise SableError(
                STEP_EXECUTION_ERROR,
                f"Subprocess exited {result.returncode}: {stderr_snippet}",
            )

        return result
