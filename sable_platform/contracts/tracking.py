"""Canonical Pydantic model for SableTracking metadata_json schema.

SableTracking writes these 17 fields to content_items.metadata_json.
Adding a field requires bumping schema_version.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class TrackingMetadata(BaseModel):
    """Schema for content_items.metadata_json written by SableTracking."""
    schema_version: int = 1

    source_tool: Literal["sable_tracking"] = "sable_tracking"
    url: Optional[str] = None
    canonical_author_handle: Optional[str] = None
    quality_score: Optional[float] = None
    audience_annotation: Optional[str] = None
    timing_annotation: Optional[str] = None
    grok_status: Optional[str] = None
    engagement_score: Optional[float] = None
    lexicon_adoption: Optional[float] = None
    emotional_valence: Optional[str] = None
    subsquad_signal: Optional[str] = None
    format_type: Optional[str] = None
    intent_type: Optional[str] = None
    topic_tags: list[str] = []
    review_status: Optional[str] = None
    outcome_type: Optional[str] = None
    is_reusable_template: bool = False
