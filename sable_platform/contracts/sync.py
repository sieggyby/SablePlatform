"""Canonical Pydantic model for sync runs."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class SyncRun(BaseModel):
    """Mirrors the sync_runs table (migrations 001 + 002)."""
    sync_id: Optional[int] = None
    org_id: str
    sync_type: str
    status: str = "running"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    records_synced: int = 0
    error: Optional[str] = None
    # Migration 002 columns
    cult_run_id: Optional[str] = None
    entities_created: int = 0
    entities_updated: int = 0
    handles_added: int = 0
    tags_added: int = 0
    tags_replaced: int = 0
    merge_candidates_created: int = 0
