"""Canonical Pydantic model for content items."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ContentItem(BaseModel):
    """Mirrors the content_items table.

    posted_at is the original event timestamp (may be null).
    created_at is the DB insert time.
    """
    item_id: str
    org_id: str
    entity_id: Optional[str] = None
    content_type: Optional[str] = None
    platform: Optional[str] = None
    external_id: Optional[str] = None
    body: Optional[str] = None
    metadata_json: str = "{}"
    posted_at: Optional[str] = None
    created_at: Optional[str] = None
