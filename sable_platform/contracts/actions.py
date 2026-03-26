"""Canonical Pydantic contracts for operator actions and outcome tracking."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class Action(BaseModel):
    action_id: str
    org_id: str
    entity_id: Optional[str] = None
    content_item_id: Optional[str] = None
    source: Literal["playbook", "strategy_brief", "pulse_meta_recommendation", "manual"] = "manual"
    source_ref: Optional[str] = None
    action_type: Literal["dm_outreach", "post_content", "reply_thread", "run_ama", "general"] = "general"
    title: str
    description: Optional[str] = None
    operator: Optional[str] = None
    status: Literal["pending", "claimed", "completed", "skipped"] = "pending"
    claimed_at: Optional[str] = None
    completed_at: Optional[str] = None
    skipped_at: Optional[str] = None
    outcome_notes: Optional[str] = None
    created_at: Optional[str] = None


class Outcome(BaseModel):
    outcome_id: str
    org_id: str
    entity_id: Optional[str] = None
    action_id: Optional[str] = None
    outcome_type: Literal[
        "client_signed", "client_churned", "entity_converted",
        "metric_change", "dm_response", "content_performance", "general"
    ]
    description: Optional[str] = None
    metric_name: Optional[str] = None
    metric_before: Optional[float] = None
    metric_after: Optional[float] = None
    metric_delta: Optional[float] = None
    data_json: Optional[str] = None
    recorded_by: Optional[str] = None
    created_at: Optional[str] = None


class DiagnosticDelta(BaseModel):
    delta_id: str
    org_id: str
    run_id_before: int
    run_id_after: int
    metric_name: str
    value_before: Optional[float] = None
    value_after: Optional[float] = None
    delta: Optional[float] = None
    pct_change: Optional[float] = None
    created_at: Optional[str] = None
