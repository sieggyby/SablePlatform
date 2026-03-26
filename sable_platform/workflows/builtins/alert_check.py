"""Workflow: alert_check — sweep all orgs for alert conditions."""
from __future__ import annotations

from sable_platform.workflows.models import StepDefinition, StepResult, WorkflowDefinition
from sable_platform.workflows import registry


def _evaluate_all_orgs(ctx) -> StepResult:
    """Run alert evaluation for all active orgs (or the configured org_id)."""
    from sable_platform.workflows.alert_evaluator import evaluate_alerts

    sweep_org_id = ctx.config.get("org_id")
    alert_ids = evaluate_alerts(ctx.db, org_id=sweep_org_id)
    return StepResult(
        "completed",
        {"alerts_created": len(alert_ids), "alert_ids": alert_ids},
    )


def _mark_complete(ctx) -> StepResult:
    return StepResult(
        "completed",
        {
            "summary": {
                "run_id": ctx.run_id,
                "org_id": ctx.org_id,
                "alerts_created": ctx.input_data.get("alerts_created", 0),
            }
        },
    )


ALERT_CHECK = WorkflowDefinition(
    name="alert_check",
    version="1.0",
    steps=[
        StepDefinition(name="evaluate_all_orgs", fn=_evaluate_all_orgs, max_retries=0),
        StepDefinition(name="mark_complete", fn=_mark_complete, max_retries=0),
    ],
)

registry.register(ALERT_CHECK)
