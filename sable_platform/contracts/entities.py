"""Canonical Pydantic models for entities, handles, and tags."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class Entity(BaseModel):
    entity_id: str
    org_id: str
    display_name: Optional[str] = None
    status: Literal["candidate", "confirmed", "archived"] = "candidate"
    source: str = "auto"
    config_json: str = "{}"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class EntityHandle(BaseModel):
    handle_id: Optional[int] = None
    entity_id: str
    platform: str
    handle: str
    is_primary: bool = False
    added_at: Optional[str] = None


class EntityTag(BaseModel):
    tag_id: Optional[int] = None
    entity_id: str
    tag: str
    source: Optional[str] = None
    confidence: float = 1.0
    is_current: bool = True
    expires_at: Optional[str] = None
    added_at: Optional[str] = None
    deactivated_at: Optional[str] = None
