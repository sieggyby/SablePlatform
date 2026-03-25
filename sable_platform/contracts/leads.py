"""Canonical Pydantic models for leads and prospect handoffs."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class Lead(BaseModel):
    """A qualified prospect from the Lead Identifier pipeline."""
    project_id: str
    name: str
    twitter_handle: Optional[str] = None
    discord_invite: Optional[str] = None
    total_raised_usd: float = 0.0
    composite_score: float = 0.0
    recommended_action: Literal["pursue", "monitor", "pass"] = "monitor"
    signal_gaps: list[str] = []
    flags: list[str] = []


class ProspectHandoff(BaseModel):
    """Input to the Cult Grader for a new diagnostic run."""
    org_id: str
    prospect_yaml_path: str
    project_name: Optional[str] = None
    twitter_handle: Optional[str] = None
    sable_org: Optional[str] = None
    lead_source: Optional[str] = None
