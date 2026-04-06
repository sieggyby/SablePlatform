"""Adapter for Sable_Community_Lead_Identifier."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal

from sable_platform.adapters.base import SubprocessAdapterMixin
from sable_platform.contracts.leads import (
    DimensionScores, Lead, PURSUE_THRESHOLD, MONITOR_THRESHOLD,
)
from sable_platform.errors import SableError, INVALID_CONFIG


def _derive_action(composite: float) -> str:
    """Derive recommended_action from composite_score."""
    if composite >= PURSUE_THRESHOLD:
        return "pursue"
    if composite >= MONITOR_THRESHOLD:
        return "monitor"
    return "pass"


def _derive_tier(composite: float) -> str:
    """Derive prospect tier from composite_score."""
    if composite >= PURSUE_THRESHOLD:
        return "Tier 1"
    if composite >= MONITOR_THRESHOLD:
        return "Tier 2"
    return "Tier 3"


def _safe_invert(gap_value, default: float = 0.5) -> float:
    """Invert a gap score to a health score. None/missing → default. Clamped to [0, 1]."""
    if gap_value is None:
        return default
    return round(max(0.0, min(1.0, 1.0 - float(gap_value))), 4)


def _clamp01(value, default: float = 0.5) -> float:
    """Clamp a score to [0, 1]. None/missing → default."""
    if value is None:
        return default
    return round(max(0.0, min(1.0, float(value))), 4)


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
        """Read sable_leads_latest.json and return Lead contracts (Tier 1 + Tier 2).

        Filters out "pass" leads (Tier 3 noise). Keeps "pursue" and "monitor"
        for the SableWeb triage view. Derives recommended_action from
        composite_score when the field is absent in the raw JSON.
        """
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
            composite = scores.get("composite", 0.0)

            # Derive action: prefer explicit field, fall back to composite thresholds
            raw_action = scores.get("recommended_action") or item.get("recommended_action")
            if raw_action in ("pursue", "monitor", "pass"):
                action = raw_action
            elif raw_action is None:
                action = _derive_action(composite)
            else:
                action = "pass"  # unknown value → treated as pass

            if action == "pass":
                continue

            # Build typed dimension scores (gap → health inversion)
            dims = DimensionScores(
                community_health=_safe_invert(scores.get("community_gap")),
                language_signal=_safe_invert(scores.get("conversation_gap")),
                growth_trajectory=_clamp01(scores.get("tge_proximity")),
                engagement_quality=_safe_invert(scores.get("engagement_gap")),
                sable_fit=_clamp01(composite),  # passthrough of composite; placeholder until dedicated score exists
            )

            lead = Lead(
                project_id=project.get("project_id", ""),
                name=project.get("name", ""),
                twitter_handle=project.get("twitter_handle"),
                discord_invite=project.get("discord_invite"),
                total_raised_usd=project.get("total_raised_usd", 0.0),
                composite_score=composite,
                recommended_action=action,
                signal_gaps=scores.get("signal_gaps", []),
                flags=item.get("flags", []),
                tier=_derive_tier(composite),
                stage="lead",
                dimensions=dims,
                rationale=scores.get("rationale"),
                enrichment=item.get("enrichment"),
                next_action=item.get("next_action"),
            )
            leads.append(lead.model_dump())

        return {"leads": leads}
