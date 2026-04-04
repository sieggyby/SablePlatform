"""Canonical Pydantic model for diagnostic runs."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class DiagnosticRun(BaseModel):
    """Mirrors the diagnostic_runs table (migrations 001 + 003)."""
    run_id: Optional[int] = None
    org_id: str
    run_type: str
    status: str = "running"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result_json: Optional[str] = None
    error: Optional[str] = None
    # Migration 003 columns
    cult_run_id: Optional[str] = None
    project_slug: Optional[str] = None
    run_date: Optional[str] = None
    research_mode: Optional[str] = None
    checkpoint_path: Optional[str] = None
    overall_grade: Optional[str] = None
    fit_score: Optional[int] = None
    recommended_action: Optional[str] = None
    sable_verdict: Optional[str] = None
    total_cost_usd: Optional[float] = None
    run_summary_json: Optional[str] = None
