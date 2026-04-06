"""Workflow 2: Weekly Client Operating Loop.

Answers for any completed run:
- Is this client's data fresh?             workflow_steps[check_tracking_freshness].output_json
- What steps were run this week?           workflow_steps table
- What outputs were generated?             workflow_steps[register_artifacts].output_json
- Which upstream sources were stale?       workflow_steps[mark_stale_artifacts].output_json
"""
from __future__ import annotations

import datetime
import json
import logging

from sable_platform.db.stale import mark_artifacts_stale
from sable_platform.errors import SableError, INVALID_CONFIG
from sable_platform.workflows.models import StepDefinition, StepResult, WorkflowDefinition
from sable_platform.workflows import registry

_DEFAULT_TRACKING_STALENESS_DAYS = 7
_DEFAULT_PULSE_STALENESS_DAYS = 14
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------

def _check_tracking_freshness(ctx) -> StepResult:
    """Query sync_runs for the latest tracking sync and compute age."""
    staleness_days = ctx.config.get("tracking_staleness_days", _DEFAULT_TRACKING_STALENESS_DAYS)

    row = ctx.db.execute(
        """
        SELECT completed_at FROM sync_runs
        WHERE org_id=? AND sync_type='sable_tracking' AND status='completed'
        ORDER BY completed_at DESC LIMIT 1
        """,
        (ctx.org_id,),
    ).fetchone()

    if row and row["completed_at"]:
        last_sync_str = row["completed_at"]
        try:
            last_sync = datetime.datetime.fromisoformat(last_sync_str.replace("Z", "+00:00"))
            now = datetime.datetime.now(datetime.timezone.utc)
            if last_sync.tzinfo is None:
                last_sync = last_sync.replace(tzinfo=datetime.timezone.utc)
            age_days = (now - last_sync).days
        except (ValueError, TypeError):
            age_days = 999
        tracking_fresh = age_days <= staleness_days
    else:
        last_sync_str = None
        age_days = 999
        tracking_fresh = False

    return StepResult(
        status="completed",
        output={
            "tracking_fresh": tracking_fresh,
            "tracking_age_days": age_days,
            "tracking_last_sync": last_sync_str,
        },
    )


def _check_pulse_freshness(ctx) -> StepResult:
    """Query sync_runs and artifacts for latest pulse/meta data and compute age.

    Checks both sync_runs (for Slopper's pulse_track/meta_scan entries) and
    artifacts (for legacy pulse_report/meta_report entries). Uses whichever
    is more recent.
    """
    staleness_days = ctx.config.get("pulse_staleness_days", _DEFAULT_PULSE_STALENESS_DAYS)

    # Check sync_runs first (Slopper writes these after pulse track / meta scan)
    sync_row = ctx.db.execute(
        """
        SELECT completed_at FROM sync_runs
        WHERE org_id=? AND sync_type IN ('pulse_track', 'meta_scan') AND status='completed'
        ORDER BY completed_at DESC LIMIT 1
        """,
        (ctx.org_id,),
    ).fetchone()

    # Also check artifacts (legacy path)
    artifact_row = ctx.db.execute(
        """
        SELECT created_at FROM artifacts
        WHERE org_id=? AND artifact_type IN ('pulse_report', 'meta_report') AND stale=0
        ORDER BY created_at DESC LIMIT 1
        """,
        (ctx.org_id,),
    ).fetchone()

    # Use the most recent timestamp from either source
    timestamps = []
    if sync_row and sync_row["completed_at"]:
        timestamps.append(sync_row["completed_at"])
    if artifact_row and artifact_row["created_at"]:
        timestamps.append(artifact_row["created_at"])

    if timestamps:
        latest_str = max(timestamps)
        try:
            latest = datetime.datetime.fromisoformat(latest_str.replace("Z", "+00:00"))
            now = datetime.datetime.now(datetime.timezone.utc)
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=datetime.timezone.utc)
            age_days = (now - latest).days
        except (ValueError, TypeError):
            age_days = 999
        pulse_fresh = age_days <= staleness_days
    else:
        age_days = 999
        pulse_fresh = False

    return StepResult(
        status="completed",
        output={
            "pulse_fresh": pulse_fresh,
            "pulse_age_days": age_days,
        },
    )


def _mark_stale_artifacts(ctx) -> StepResult:
    """Mark downstream artifacts stale when tracking data is stale."""
    types = ["twitter_strategy_brief", "discord_playbook"]
    mark_artifacts_stale(ctx.db, ctx.org_id, types)
    return StepResult(
        status="completed",
        output={"stale_artifact_types": types},
    )


def _trigger_tracking_sync(ctx) -> StepResult:
    """Invoke SableTracking adapter to refresh contributor/content data."""
    from sable_platform.adapters.tracking_sync import SableTrackingAdapter

    adapter = SableTrackingAdapter()
    result = adapter.run({"org_id": ctx.org_id})
    return StepResult(
        status="completed",
        output={"tracking_sync_result": result},
    )


def _trigger_strategy_generation(ctx) -> StepResult:
    """Invoke Slopper advisory adapter to generate strategy brief."""
    from sable_platform.adapters.slopper import SlopperAdvisoryAdapter

    adapter = SlopperAdvisoryAdapter()
    result = adapter.run({"org_id": ctx.org_id})
    return StepResult(
        status="completed",
        output={"strategy_result": result},
    )


def _get_run_started_at(ctx) -> str | None:
    """Return the started_at timestamp for the current workflow run."""
    row = ctx.db.execute(
        "SELECT started_at FROM workflow_runs WHERE run_id = ?",
        (ctx.run_id,),
    ).fetchone()
    return row["started_at"] if row else None


def _register_artifacts(ctx) -> StepResult:
    """Count non-stale artifacts produced for this org during the workflow run.

    The Slopper adapter's run() returns {status, job_ref, org_id} — it does NOT
    return artifact paths. Slopper writes artifacts directly to sable.db via
    log_cost/artifact registration in generate_advise(). So we query the
    artifacts table for recent non-stale entries created since this run started.
    """
    started_at = _get_run_started_at(ctx)
    if not started_at:
        log.warning("run %s has no started_at — cannot scope artifact query", ctx.run_id)
        return StepResult(
            status="completed",
            output={"artifact_ids": [], "artifact_count": 0},
        )

    rows = ctx.db.execute(
        """
        SELECT artifact_id FROM artifacts
        WHERE org_id = ? AND stale = 0 AND created_at >= ?
        ORDER BY created_at DESC LIMIT 20
        """,
        (ctx.org_id, started_at),
    ).fetchall()

    artifact_ids = [r["artifact_id"] for r in rows]

    return StepResult(
        status="completed",
        output={"artifact_ids": artifact_ids, "artifact_count": len(artifact_ids)},
    )


def _parse_actions_from_artifact(ctx, artifact_type: str, source: str, action_type: str) -> list[str]:
    """Parse action items from the latest artifact of a given type created during this run.

    Returns list of created action_ids.
    """
    import re
    from sable_platform.db.actions import create_action

    started_at = _get_run_started_at(ctx)
    if not started_at:
        log.warning("run %s has no started_at — cannot scope action query", ctx.run_id)
        return []

    row = ctx.db.execute(
        """
        SELECT artifact_id, path FROM artifacts
        WHERE org_id=? AND artifact_type=? AND created_at >= ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (ctx.org_id, artifact_type, started_at),
    ).fetchone()

    if not row or not row["path"]:
        log.warning(
            "No artifact path for %s (org %s, run %s) — zero actions registered",
            artifact_type, ctx.org_id, ctx.run_id,
        )
        return []

    artifact_path = row["path"]
    artifact_ref = row["artifact_id"]

    try:
        with open(artifact_path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        log.warning("%s file not found at %s — no actions registered", artifact_type, artifact_path)
        return []

    action_ids = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if re.match(r"^#{1,3}\s+(Actions|Recommendations|This Week)", stripped, re.I):
            in_section = True
            continue
        if re.match(r"^#{1,3}", stripped):
            in_section = False
        if in_section and re.match(r"^[-*\d]", stripped):
            title = re.sub(r"^[-*\d.]+\s*", "", stripped)[:200].strip()
            if title:
                aid = create_action(
                    ctx.db, ctx.org_id, title,
                    source=source,
                    source_ref=str(artifact_ref),
                    action_type=action_type,
                )
                action_ids.append(aid)

    return action_ids


def _register_actions(ctx) -> StepResult:
    """Parse latest playbook and strategy brief artifacts, create action rows for recommendations."""
    action_ids = []
    action_ids.extend(
        _parse_actions_from_artifact(ctx, "discord_playbook", "playbook", "general")
    )
    action_ids.extend(
        _parse_actions_from_artifact(ctx, "twitter_strategy_brief", "strategy_brief", "post_content")
    )

    return StepResult("completed", {"action_ids": action_ids, "actions_created": len(action_ids)})


def _evaluate_alerts(ctx) -> StepResult:
    """Run alert evaluation for this org."""
    from sable_platform.workflows.alert_evaluator import evaluate_alerts
    alert_ids = evaluate_alerts(ctx.db, org_id=ctx.org_id)
    return StepResult("completed", {"alerts_created": len(alert_ids), "alert_ids": alert_ids})


def _mark_complete(ctx) -> StepResult:
    """Return freshness summary."""
    return StepResult(
        status="completed",
        output={
            "summary": {
                "run_id": ctx.run_id,
                "org_id": ctx.org_id,
                "tracking_fresh": ctx.input_data.get("tracking_fresh"),
                "tracking_age_days": ctx.input_data.get("tracking_age_days"),
                "pulse_fresh": ctx.input_data.get("pulse_fresh"),
                "pulse_age_days": ctx.input_data.get("pulse_age_days"),
                "artifact_count": ctx.input_data.get("artifact_count", 0),
                "actions_created": ctx.input_data.get("actions_created", 0),
            }
        },
    )


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------

WEEKLY_CLIENT_LOOP = WorkflowDefinition(
    name="weekly_client_loop",
    version="1.0",
    steps=[
        StepDefinition(name="check_tracking_freshness", fn=_check_tracking_freshness, max_retries=0),
        StepDefinition(name="check_pulse_freshness", fn=_check_pulse_freshness, max_retries=0),
        StepDefinition(
            name="mark_stale_artifacts",
            fn=_mark_stale_artifacts,
            max_retries=0,
            skip_if=lambda ctx: ctx.input_data.get("tracking_fresh", False) is True,
        ),
        StepDefinition(
            name="trigger_tracking_sync",
            fn=_trigger_tracking_sync,
            max_retries=1,
            skip_if=lambda ctx: ctx.input_data.get("tracking_fresh", False) is True,
        ),
        StepDefinition(name="trigger_strategy_generation", fn=_trigger_strategy_generation, max_retries=1),
        StepDefinition(name="register_artifacts", fn=_register_artifacts, max_retries=1),
        StepDefinition(name="register_actions", fn=_register_actions, max_retries=0),
        StepDefinition(name="evaluate_alerts", fn=_evaluate_alerts, max_retries=0),
        StepDefinition(name="mark_complete", fn=_mark_complete, max_retries=0),
    ],
)

registry.register(WEEKLY_CLIENT_LOOP)
