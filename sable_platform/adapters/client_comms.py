"""Adapter for Sable_Client_Comms.

V1 stub. Sable_Client_Comms is the architectural-boundary placeholder for
client-facing communications (check-in synthesis, send, archive). For the
TIG trial build, real LLM logic + delivery live in `sable_platform.checkin`
and `workflows.builtins.client_checkin_loop`. This adapter exists so the
boundary is in place when that logic migrates post-trial.

The adapter shells out to the `sable-comms` console script in the target
repo's venv. V1 returns the script's JSON payload verbatim.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from sable_platform.adapters.base import SubprocessAdapterMixin

log = logging.getLogger(__name__)


class SableClientCommsAdapter(SubprocessAdapterMixin):
    name = "client_comms"

    def _repo_path(self) -> Path:
        return self._resolve_repo_path("SABLE_CLIENT_COMMS_PATH")

    def _comms_command(self, repo: Path, argv: list[str]) -> list[str]:
        """Prefer the repo's `sable-comms` console script; fall back to module form."""
        console = repo / ".venv" / "bin" / "sable-comms"
        if console.exists():
            return [str(console), *argv]
        return [self._python_for(repo), "-m", "sable_client_comms.cli", *argv]

    def run(self, input_data: dict) -> dict:
        """Invoke the stub CLI. V1 always returns synchronously.

        ``input_data`` accepts ``argv`` (list[str]) for forward-compat. Anything
        else is forwarded as ``--key value`` pairs so the same shape works once
        real subcommands land.
        """
        repo = self._repo_path()
        argv = list(input_data.get("argv") or [])
        if not argv:
            argv = ["noop"]
            for key, val in input_data.items():
                if key == "argv":
                    continue
                argv.extend([f"--{key}", str(val)])

        result = self._run_subprocess(self._comms_command(repo, argv), cwd=repo, timeout=120)

        payload: dict
        try:
            payload = json.loads((result.stdout or "").strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            payload = {"raw_stdout": result.stdout}

        return {"status": "completed", "job_ref": "noop", "payload": payload}

    def status(self, job_ref: str) -> Literal["pending", "running", "completed", "failed"]:
        """Stub adapter is synchronous — every job is already complete."""
        return "completed"

    def get_result(self, job_ref: str) -> dict:
        """Stub adapter has no async result store; the run() return is authoritative."""
        return {"job_ref": job_ref, "noop": True}
