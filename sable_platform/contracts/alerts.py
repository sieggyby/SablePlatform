"""Canonical Pydantic contracts for proactive alerting."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class AlertConfig(BaseModel):
    config_id: str
    org_id: str
    min_severity: Literal["critical", "warning", "info"] = "warning"
    telegram_chat_id: Optional[str] = None
    discord_webhook_url: Optional[str] = None
    enabled: bool = True
    cooldown_hours: int = 4
    created_at: Optional[str] = None


class Alert(BaseModel):
    alert_id: str
    org_id: Optional[str] = None
    alert_type: str
    severity: Literal["critical", "warning", "info"]
    title: str
    body: Optional[str] = None
    entity_id: Optional[str] = None
    action_id: Optional[str] = None
    run_id: Optional[str] = None
    data_json: Optional[str] = None
    status: Literal["new", "acknowledged", "resolved"] = "new"
    dedup_key: Optional[str] = None
    last_delivered_at: Optional[str] = None
    last_delivery_error: Optional[str] = None
    acknowledged_at: Optional[str] = None
    acknowledged_by: Optional[str] = None
    resolved_at: Optional[str] = None
    created_at: Optional[str] = None
