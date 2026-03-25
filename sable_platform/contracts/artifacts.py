"""Canonical Pydantic model for artifacts."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class Artifact(BaseModel):
    """Mirrors the artifacts table (migrations 001 + 005)."""
    artifact_id: Optional[int] = None
    org_id: str
    job_id: Optional[str] = None
    artifact_type: str
    path: Optional[str] = None
    metadata_json: str = "{}"
    stale: bool = False
    degraded: bool = False
    created_at: Optional[str] = None
