"""Workflow: onboard_client — verify all adapters and create initial sync record."""
from __future__ import annotations

import sqlite3
import uuid

from sqlalchemy.exc import DatabaseError as SADatabaseError

from sable_platform.errors import SableError, INVALID_CONFIG
from sable_platform.workflows.models import StepDefinition, StepResult, WorkflowDefinition
from sable_platform.workflows import registry


def _verify_org(ctx) -> StepResult:
    """Check that the org exists in sable.db."""
    row = ctx.db.execute("SELECT 1 FROM orgs WHERE org_id=?", (ctx.org_id,)).fetchone()
    if not row:
        raise SableError(INVALID_CONFIG, f"Org '{ctx.org_id}' not found in sable.db")
    return StepResult("completed", {"org_verified": True})


def _verify_tracking_adapter(ctx) -> StepResult:
    """Read-only env check for SableTrackingAdapter."""
    from sable_platform.adapters.tracking_sync import SableTrackingAdapter
    adapter = SableTrackingAdapter()
    try:
        adapter._repo_path()
        return StepResult("completed", {"tracking_adapter": "ok"})
    except SableError as e:
        return StepResult("completed", {"tracking_adapter": f"fail: {e.message}"})


def _verify_slopper_adapter(ctx) -> StepResult:
    """Read-only env check for SlopperAdvisoryAdapter."""
    from sable_platform.adapters.slopper import SlopperAdvisoryAdapter
    adapter = SlopperAdvisoryAdapter()
    try:
        adapter._repo_path()
        return StepResult("completed", {"slopper_adapter": "ok"})
    except SableError as e:
        return StepResult("completed", {"slopper_adapter": f"fail: {e.message}"})


def _verify_cult_grader_adapter(ctx) -> StepResult:
    """Read-only env check for CultGraderAdapter."""
    from sable_platform.adapters.cult_grader import CultGraderAdapter
    adapter = CultGraderAdapter()
    try:
        adapter._repo_path()
        return StepResult("completed", {"cult_grader_adapter": "ok"})
    except SableError as e:
        return StepResult("completed", {"cult_grader_adapter": f"fail: {e.message}"})


def _verify_lead_identifier_adapter(ctx) -> StepResult:
    """Read-only env check for LeadIdentifierAdapter."""
    import os
    from pathlib import Path
    path = os.environ.get("SABLE_LEAD_IDENTIFIER_PATH", "")
    if not path:
        return StepResult("completed", {"lead_identifier_adapter": "fail: SABLE_LEAD_IDENTIFIER_PATH not set"})
    if not Path(path).exists():
        return StepResult("completed", {"lead_identifier_adapter": f"fail: path does not exist: {path}"})
    return StepResult("completed", {"lead_identifier_adapter": "ok"})


def _create_initial_sync_record(ctx) -> StepResult:
    """Insert a sync_runs row with status='pending' and sync_type='onboarding'."""
    cult_run_id = uuid.uuid4().hex
    try:
        ctx.db.execute(
            """
            INSERT INTO sync_runs (org_id, sync_type, cult_run_id, started_at, status, records_synced)
            VALUES (?, 'onboarding', ?, CURRENT_TIMESTAMP, 'pending', 0)
            """,
            (ctx.org_id, cult_run_id),
        )
        ctx.db.commit()
        sync_run_id = ctx.db.execute("SELECT last_insert_rowid()").fetchone()[0]
    except (sqlite3.Error, SADatabaseError) as exc:
        raise SableError(INVALID_CONFIG, f"sync_run insert failed: {exc}") from exc
    return StepResult("completed", {"sync_run_id": sync_run_id})


def _mark_complete(ctx) -> StepResult:
    """Return structured readiness report."""
    tools_verified = []
    tools_failed = []
    for key, label in [
        ("tracking_adapter", "tracking"),
        ("slopper_adapter", "slopper"),
        ("cult_grader_adapter", "cult_grader"),
        ("lead_identifier_adapter", "lead_identifier"),
    ]:
        val = ctx.input_data.get(key, "")
        if val == "ok":
            tools_verified.append(label)
        elif val:
            tools_failed.append(label)
    return StepResult(
        "completed",
        {
            "summary": {
                "org_id": ctx.org_id,
                "tools_verified": tools_verified,
                "tools_failed": tools_failed,
                "sync_run_id": ctx.input_data.get("sync_run_id"),
            }
        },
    )


ONBOARD_CLIENT = WorkflowDefinition(
    name="onboard_client",
    version="1.0",
    steps=[
        StepDefinition(name="verify_org", fn=_verify_org, max_retries=0),
        StepDefinition(name="verify_tracking_adapter", fn=_verify_tracking_adapter, max_retries=0),
        StepDefinition(name="verify_slopper_adapter", fn=_verify_slopper_adapter, max_retries=0),
        StepDefinition(name="verify_cult_grader_adapter", fn=_verify_cult_grader_adapter, max_retries=0),
        StepDefinition(name="verify_lead_identifier_adapter", fn=_verify_lead_identifier_adapter, max_retries=0),
        StepDefinition(name="create_initial_sync_record", fn=_create_initial_sync_record, max_retries=0),
        StepDefinition(name="mark_complete", fn=_mark_complete, max_retries=0),
    ],
)

registry.register(ONBOARD_CLIENT)
