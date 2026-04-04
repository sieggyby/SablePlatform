"""Canonical Pydantic models for workflow runs, steps, and events."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class WorkflowRun(BaseModel):
    """Mirrors the workflow_runs table (migration 006)."""
    run_id: str
    org_id: str
    workflow_name: str
    workflow_version: str = "1.0"
    status: Literal["pending", "running", "completed", "failed", "cancelled", "timed_out"] = "pending"
    config_json: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    step_fingerprint: Optional[str] = None
    created_at: Optional[str] = None


class WorkflowStep(BaseModel):
    """Mirrors the workflow_steps table (migration 006)."""
    step_id: str
    run_id: str
    step_name: str
    step_index: int
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    retries: int = 0
    input_json: Optional[str] = None
    output_json: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class WorkflowEvent(BaseModel):
    """Mirrors the workflow_events table (migration 006)."""
    event_id: str
    run_id: str
    step_id: Optional[str] = None
    event_type: str
    payload_json: Optional[str] = None
    created_at: Optional[str] = None
