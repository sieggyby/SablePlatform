"""Adapter base types and subprocess mixin."""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Literal, Protocol

from sable_platform.errors import SableError, INVALID_CONFIG, STEP_EXECUTION_ERROR

logger = logging.getLogger(__name__)


class AdapterBase(Protocol):
    name: str

    def run(self, input_data: dict) -> dict: ...
    def status(self, job_ref: str, conn=None) -> Literal["pending", "running", "completed", "failed"]: ...
    def get_result(self, job_ref: str, conn=None) -> dict: ...


class SubprocessAdapterMixin:
    """Mixin providing a safe subprocess runner for adapter implementations."""

    def _resolve_repo_path(self, env_var: str) -> Path:
        """Resolve a repo path from an env var, raising SableError on missing/invalid."""
        val = os.environ.get(env_var)
        if not val:
            raise SableError(INVALID_CONFIG, f"{env_var} environment variable is not set")
        p = Path(val)
        if not p.is_dir():
            raise SableError(INVALID_CONFIG, f"{env_var} does not exist: {p}")
        return p

    def _python_for(self, repo: Path) -> str:
        """Interpreter to use when subprocessing into a target repo.

        Each Sable subrepo carries its own deps in <repo>/.venv, so the platform
        venv's sys.executable can't import them. Prefer the repo's own venv
        python; fall back to sys.executable only when no venv is present.
        """
        venv_py = repo / ".venv" / "bin" / "python"
        if venv_py.exists():
            return str(venv_py)
        return sys.executable

    def _run_subprocess(
        self,
        cmd: list[str],
        cwd: Path,
        timeout: int = 1800,
        env: dict | None = None,
    ) -> subprocess.CompletedProcess:
        """Run a subprocess synchronously with process group isolation.

        Uses start_new_session=True so the child gets its own process group.
        On timeout, kills the entire process group (os.killpg) to prevent
        orphaned grandchild processes.
        """
        adapter_name = getattr(self, "name", "unknown")
        logger.info("Adapter subprocess start: %s (cwd=%s)", " ".join(cmd), cwd,
                     extra={"adapter": adapter_name})
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
            )
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the entire process group to prevent orphaned grandchild processes
            if proc is not None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass  # process group already exited
                proc.wait()
            logger.warning("Adapter subprocess timed out after %ds, process group killed: %s",
                           timeout, " ".join(cmd), extra={"adapter": adapter_name})
            raise SableError(
                STEP_EXECUTION_ERROR,
                f"Subprocess timed out after {timeout}s: {' '.join(cmd)}",
            )
        except FileNotFoundError as exc:
            raise SableError(
                STEP_EXECUTION_ERROR,
                f"Command not found: {cmd[0]}",
            ) from exc

        result = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

        if result.returncode != 0:
            stderr_snippet = (result.stderr or "")[:500]
            logger.debug("Subprocess stderr: %s", result.stderr)
            raise SableError(
                STEP_EXECUTION_ERROR,
                f"Subprocess exited {result.returncode}: {stderr_snippet}",
            )

        return result
