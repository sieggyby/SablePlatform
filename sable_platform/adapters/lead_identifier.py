"""Adapter for Sable_Community_Lead_Identifier."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal

from sable_platform.adapters.base import SubprocessAdapterMixin
from sable_platform.contracts.leads import Lead
from sable_platform.errors import SableError, INVALID_CONFIG


class LeadIdentifierAdapter(SubprocessAdapterMixin):
    name = "lead_identifier"

    def _repo_path(self) -> Path:
        return self._resolve_repo_path("SABLE_LEAD_IDENTIFIER_PATH")

    def run(self, input_data: dict) -> dict:
        """Run the Lead Identifier pipeline (pass-1 only by default). Blocks until done."""
        repo = self._repo_path()
        pass1_only = input_data.get("pass1_only", True)
        cmd = [sys.executable, "main.py", "run"]
        if pass1_only:
            cmd.append("--pass1-only")

        self._run_subprocess(cmd, cwd=repo, timeout=3600)
        return {"status": "completed", "job_ref": "latest", "output_dir": str(repo / "output")}

    def status(self, job_ref: str) -> Literal["pending", "running", "completed", "failed"]:
        """Check if output file exists."""
        repo_env = os.environ.get("SABLE_LEAD_IDENTIFIER_PATH", "")
        latest = Path(repo_env) / "output" / "sable_leads_latest.json"
        if latest.exists():
            return "completed"
        return "pending"

    def get_result(self, job_ref: str) -> dict:
        """Read sable_leads_latest.json and return as Lead contracts filtered to 'pursue'."""
        repo_env = os.environ.get("SABLE_LEAD_IDENTIFIER_PATH", "")
        latest = Path(repo_env) / "output" / "sable_leads_latest.json"
        if not latest.exists():
            # Fall back to most recently modified dated file
            output_dir = Path(repo_env) / "output"
            candidates = sorted(output_dir.glob("sable_leads_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not candidates:
                return {"leads": []}
            latest = candidates[0]

        raw = json.loads(latest.read_text(encoding="utf-8"))
        leads: list[dict] = []
        entries = raw.get("leads", []) if isinstance(raw, dict) else raw
        for item in entries:
            # Lead Identifier JSON envelope: {"run_id", "generated_at", "leads": [RankedProject...]}
            # RankedProject shape: {"rank": ..., "project": {...}, "scores": {...}, "flags": [...]}
            project = item.get("project", {})
            scores = item.get("scores", {})
            action = scores.get("recommended_action") or item.get("recommended_action", "monitor")
            if action != "pursue":
                continue
            lead = Lead(
                project_id=project.get("project_id", ""),
                name=project.get("name", ""),
                twitter_handle=project.get("twitter_handle"),
                discord_invite=project.get("discord_invite"),
                total_raised_usd=project.get("total_raised_usd", 0.0),
                composite_score=scores.get("composite", 0.0),
                recommended_action="pursue",
                flags=item.get("flags", []),
            )
            leads.append(lead.model_dump())

        return {"leads": leads}
