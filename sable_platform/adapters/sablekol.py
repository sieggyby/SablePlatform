"""Adapter for SableKOL — Phase 0 stub.

Wraps SableKOL's CLI (``sable-kol``) via the standard subprocess pattern. Phase 0
exposes ``ingest``, ``classify``, ``crossref``, and ``find`` operations. The
adapter is NOT wired into orchestration yet — it exists so cron-style flows can
opt in once SableKOL stabilizes.

Repo path is resolved from ``SABLE_KOL_PATH``. The interpreter is
``<repo>/.venv/bin/python`` per the suite-wide convention (see
``SubprocessAdapterMixin._python_for``); using ``sys.executable`` would miss
SableKOL's deps and is forbidden.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from sable_platform.adapters.base import SubprocessAdapterMixin


logger = logging.getLogger(__name__)


class SableKOLAdapter(SubprocessAdapterMixin):
    name = "sablekol"

    # ------------------------------------------------------------------
    # Path + entry-point resolution
    # ------------------------------------------------------------------

    def _repo_path(self) -> Path:
        return self._resolve_repo_path("SABLE_KOL_PATH")

    def _cli(self, repo: Path) -> list[str]:
        """``<venv-python> -m sable_kol.cli`` — survives a missing
        ``sable-kol`` script entry point and works whether SableKOL is
        editable-installed or not."""
        return [self._python_for(repo), "-m", "sable_kol.cli"]

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def ingest(self, *, list_export: str, source_id: str = "cahit_list") -> dict:
        repo = self._repo_path()
        cmd = self._cli(repo) + ["ingest", "--list-export", list_export, "--source-id", source_id]
        result = self._run_subprocess(cmd, cwd=repo, timeout=300)
        return {"status": "completed", "stdout": result.stdout}

    def classify(self, *, limit: int | None = None, force: bool = False) -> dict:
        repo = self._repo_path()
        cmd = self._cli(repo) + ["classify"]
        if limit is not None:
            cmd += ["--limit", str(limit)]
        if force:
            cmd += ["--force"]
        result = self._run_subprocess(cmd, cwd=repo, timeout=1800)
        return {"status": "completed", "stdout": result.stdout}

    def crossref(self) -> dict:
        repo = self._repo_path()
        cmd = self._cli(repo) + ["crossref"]
        result = self._run_subprocess(cmd, cwd=repo, timeout=600)
        return {"status": "completed", "stdout": result.stdout}

    def find(
        self,
        *,
        org_id: str | None = None,
        external_handle: str | None = None,
        sector: str | None = None,
        themes: list[str] | None = None,
        paid_enrich: bool = False,
        limit: int = 20,
    ) -> dict:
        """Run ``sable-kol find`` and parse the JSON output.

        Returns the canonical FindOutput dict (``project``, ``results``,
        ``query_metadata``).
        """
        repo = self._repo_path()
        cmd = self._cli(repo) + ["find", "--output", "json", "--limit", str(limit)]
        if org_id:
            cmd += ["--org", org_id]
        elif external_handle:
            if not sector:
                raise ValueError("external_handle requires sector")
            cmd += ["--handle", external_handle, "--sector", sector]
        else:
            raise ValueError("either org_id or external_handle is required")
        if themes:
            cmd += ["--themes", ",".join(themes)]
        if paid_enrich:
            cmd += ["--paid-enrich"]

        result = self._run_subprocess(cmd, cwd=repo, timeout=600)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            logger.error("sable-kol find returned non-JSON: %s", result.stdout[:500])
            raise RuntimeError(f"sable-kol find emitted non-JSON output: {exc}") from exc
