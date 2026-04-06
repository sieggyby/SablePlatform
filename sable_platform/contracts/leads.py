"""Canonical Pydantic models for leads and prospect handoffs."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

# Tier derivation thresholds — shared by adapter and workflow sync.
# Matches Lead Identifier's platform_sync.py.
PURSUE_THRESHOLD = 0.70
MONITOR_THRESHOLD = 0.55


class DimensionScores(BaseModel):
    """Typed dimension scores for prospect evaluation.

    Gap-to-health inversion happens at the adapter boundary:
    community_gap → community_health (1.0 - gap), etc.
    """
    community_health: float = 0.5
    language_signal: float = 0.5
    growth_trajectory: float = 0.5
    engagement_quality: float = 0.5
    sable_fit: float = 0.5


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
    tier: str = "Tier 3"
    stage: str = "lead"
    dimensions: DimensionScores = DimensionScores()
    rationale: Optional[dict] = None
    enrichment: Optional[dict] = None
    next_action: Optional[str] = None


class ProspectHandoff(BaseModel):
    """Input to the Cult Grader for a new diagnostic run."""
    org_id: str
    prospect_yaml_path: str
    project_name: Optional[str] = None
    twitter_handle: Optional[str] = None
    sable_org: Optional[str] = None
    lead_source: Optional[str] = None
