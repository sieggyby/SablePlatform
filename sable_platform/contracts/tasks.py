"""Canonical Pydantic models for tasks, outcomes, and recommendations."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class Task(BaseModel):
    task_id: Optional[str] = None
    org_id: str
    run_id: Optional[str] = None
    task_type: str
    priority: Literal["high", "medium", "low"] = "medium"
    title: str
    description: Optional[str] = None
    status: Literal["open", "in_progress", "done", "cancelled"] = "open"
    due_date: Optional[str] = None
    created_at: Optional[str] = None


class RunOutcome(BaseModel):
    outcome_id: Optional[str] = None
    run_id: Optional[str] = None
    org_id: str
    outcome_type: str
    description: Optional[str] = None
    data: Optional[dict] = None
    created_at: Optional[str] = None


class Recommendation(BaseModel):
    rec_id: Optional[str] = None
    org_id: str
    run_id: Optional[str] = None
    text: str
    action_type: Optional[str] = None
    created_at: Optional[str] = None
