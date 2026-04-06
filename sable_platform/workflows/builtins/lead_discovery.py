"""Workflow: Lead Discovery — run Lead Identifier and create entities for pursue leads.

Answers for any completed run:
- How many leads were found?             workflow_steps[parse_leads].output_json → lead_count
- Which entities were created?           workflow_steps[create_entities].output_json → entity_ids
- What was the output artifact?          workflow_steps[register_artifacts].output_json → artifact_ids
"""
from __future__ import annotations

import datetime
import json
import logging

from sable_platform.errors import SableError, INVALID_CONFIG
from sable_platform.workflows.models import StepDefinition, StepResult, WorkflowDefinition
from sable_platform.workflows import registry

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------

def _validate_env(ctx) -> StepResult:
    """Confirm SABLE_LEAD_IDENTIFIER_PATH is set and org exists."""
    import os
    path = os.environ.get("SABLE_LEAD_IDENTIFIER_PATH")
    if not path:
        raise SableError(INVALID_CONFIG, "SABLE_LEAD_IDENTIFIER_PATH environment variable is not set")

    from pathlib import Path
    if not Path(path).is_dir():
        raise SableError(INVALID_CONFIG, f"SABLE_LEAD_IDENTIFIER_PATH does not exist: {path}")

    row = ctx.db.execute("SELECT 1 FROM orgs WHERE org_id=?", (ctx.org_id,)).fetchone()
    if not row:
        raise SableError(INVALID_CONFIG, f"Org '{ctx.org_id}' not found in DB")

    return StepResult("completed", {"lead_identifier_path": path})


def _run_lead_identifier(ctx) -> StepResult:
    """Execute the Lead Identifier pipeline (pass-1 by default)."""
    from sable_platform.adapters.lead_identifier import LeadIdentifierAdapter

    pass1_only = ctx.config.get("pass1_only", True)
    adapter = LeadIdentifierAdapter()
    result = adapter.run({"pass1_only": pass1_only})
    return StepResult("completed", {
        "lead_output_dir": result.get("output_dir", ""),
        "lead_job_ref": result.get("job_ref", "latest"),
    })


def _parse_leads(ctx) -> StepResult:
    """Read Lead Identifier output and return parsed pursue-leads."""
    from sable_platform.adapters.lead_identifier import LeadIdentifierAdapter

    adapter = LeadIdentifierAdapter()
    result = adapter.get_result(ctx.input_data.get("lead_job_ref", "latest"))
    leads = result.get("leads", [])
    return StepResult("completed", {
        "leads": leads,
        "lead_count": len(leads),
    })


def _create_entities(ctx) -> StepResult:
    """Create or locate entities for each pursue lead, tag as bd_prospect."""
    from sable_platform.db.entities import create_entity, add_handle
    from sable_platform.db.tags import add_tag

    leads = ctx.input_data.get("leads", [])
    entity_ids: list[str] = []
    created = 0
    existing = 0

    for lead in leads:
        twitter = lead.get("twitter_handle", "")
        name = lead.get("name", lead.get("project_id", "unknown"))

        # Check for existing entity by twitter handle
        entity_id = None
        if twitter:
            handle_norm = twitter.lower().lstrip("@").strip()
            row = ctx.db.execute(
                """
                SELECT e.entity_id FROM entities e
                JOIN entity_handles h ON e.entity_id = h.entity_id
                WHERE e.org_id=? AND h.platform='twitter' AND h.handle=?
                """,
                (ctx.org_id, handle_norm),
            ).fetchone()
            if row:
                entity_id = row["entity_id"]
                existing += 1

        if entity_id is None:
            entity_id = create_entity(
                ctx.db,
                ctx.org_id,
                display_name=name,
                status="candidate",
                source="lead_identifier",
            )
            created += 1

            if twitter:
                handle_norm = twitter.lower().lstrip("@").strip()
                try:
                    add_handle(ctx.db, entity_id, "twitter", handle_norm)
                except SableError:
                    pass

            discord = lead.get("discord_invite", "")
            if discord:
                try:
                    add_handle(ctx.db, entity_id, "discord", discord.strip())
                except SableError:
                    pass

        add_tag(ctx.db, entity_id, "bd_prospect", source="lead_identifier",
                confidence=lead.get("composite_score", 1.0))

        entity_ids.append(entity_id)

    ctx.db.commit()
    return StepResult("completed", {
        "entity_ids": entity_ids,
        "entities_created": created,
        "entities_existing": existing,
    })


def _sync_scores(ctx) -> StepResult:
    """Sync parsed leads to prospect_scores table.

    Reads typed dimension scores from the Lead contract (populated by the
    adapter with gap→health inversion already applied). Derives tier from
    composite_score using the same 0.70/0.55 thresholds as platform_sync.py.
    """
    from sable_platform.db.prospects import sync_prospect_scores
    from sable_platform.contracts.leads import PURSUE_THRESHOLD, MONITOR_THRESHOLD

    leads = ctx.input_data.get("leads", [])
    if not leads:
        return StepResult("completed", {"scores_synced": 0})

    run_date = datetime.date.today().isoformat()
    scores = []
    for lead in leads:
        composite = lead.get("composite_score", 0.0)

        # Dimensions already populated by adapter (typed DimensionScores)
        dims = lead.get("dimensions", {})
        dimensions = {
            "community_health": dims.get("community_health", 0.5),
            "language_signal": dims.get("language_signal", 0.5),
            "growth_trajectory": dims.get("growth_trajectory", 0.5),
            "engagement_quality": dims.get("engagement_quality", 0.5),
            "sable_fit": dims.get("sable_fit", 0.5),
        }

        # Derive tier from composite_score (canonical thresholds from contracts)
        if composite >= PURSUE_THRESHOLD:
            tier = "Tier 1"
        elif composite >= MONITOR_THRESHOLD:
            tier = "Tier 2"
        else:
            tier = "Tier 3"

        scores.append({
            "org_id": lead.get("project_id", lead.get("name", "unknown")),
            "composite_score": composite,
            "tier": tier,
            "stage": lead.get("stage", "lead"),
            "dimensions": dimensions,
            "rationale": lead.get("rationale"),
            "enrichment": lead.get("enrichment"),
            "next_action": lead.get("next_action"),
        })

    count = sync_prospect_scores(ctx.db, scores, run_date)
    return StepResult("completed", {"scores_synced": count})


def _register_artifacts(ctx) -> StepResult:
    """Register the Lead Identifier output directory as an artifact."""
    output_dir = ctx.input_data.get("lead_output_dir", "")
    artifact_ids: list[int] = []

    if output_dir:
        from pathlib import Path
        # Register the latest JSON file if present
        latest = Path(output_dir) / "sable_leads_latest.json"
        path_to_register = str(latest) if latest.exists() else output_dir
        cur = ctx.db.execute(
            """
            INSERT INTO artifacts (org_id, artifact_type, path, metadata_json)
            VALUES (?, 'lead_identifier_output', ?, ?)
            """,
            (
                ctx.org_id,
                path_to_register,
                json.dumps({"source": "lead_discovery", "run_id": ctx.run_id,
                            "lead_count": ctx.input_data.get("lead_count", 0)}),
            ),
        )
        artifact_ids.append(cur.lastrowid)
        ctx.db.commit()

    return StepResult("completed", {"artifact_ids": artifact_ids, "artifact_count": len(artifact_ids)})


def _evaluate_alerts(ctx) -> StepResult:
    """Run alert evaluation for this org."""
    from sable_platform.workflows.alert_evaluator import evaluate_alerts
    from sable_platform.workflows.alert_delivery import deliver_alerts_by_ids
    alert_ids = evaluate_alerts(ctx.db, org_id=ctx.org_id)
    deliver_alerts_by_ids(ctx.db, alert_ids)
    return StepResult("completed", {"alerts_created": len(alert_ids), "alert_ids": alert_ids})


def _mark_complete(ctx) -> StepResult:
    """Return a summary of the completed run."""
    return StepResult("completed", {
        "summary": {
            "run_id": ctx.run_id,
            "org_id": ctx.org_id,
            "lead_count": ctx.input_data.get("lead_count", 0),
            "entities_created": ctx.input_data.get("entities_created", 0),
            "entities_existing": ctx.input_data.get("entities_existing", 0),
            "artifact_count": ctx.input_data.get("artifact_count", 0),
        }
    })


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------

LEAD_DISCOVERY = WorkflowDefinition(
    name="lead_discovery",
    version="1.0",
    steps=[
        StepDefinition(name="validate_env", fn=_validate_env, max_retries=0),
        StepDefinition(name="run_lead_identifier", fn=_run_lead_identifier, max_retries=1),
        StepDefinition(name="parse_leads", fn=_parse_leads, max_retries=1),
        StepDefinition(name="create_entities", fn=_create_entities, max_retries=1),
        StepDefinition(name="sync_scores", fn=_sync_scores, max_retries=0),
        StepDefinition(name="register_artifacts", fn=_register_artifacts, max_retries=1),
        StepDefinition(name="evaluate_alerts", fn=_evaluate_alerts, max_retries=0),
        StepDefinition(name="mark_complete", fn=_mark_complete, max_retries=0),
    ],
)

registry.register(LEAD_DISCOVERY)
