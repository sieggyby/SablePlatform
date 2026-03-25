"""Workflow 1: Prospect → Diagnostic → Entity Sync.

Answers for any completed run:
- When was this project discovered?        workflow_steps[validate_prospect].started_at
- When was it diagnosed?                   diagnostic_runs.completed_at
- What entity/artifact IDs exist?          workflow_steps[register_artifacts].output_json
- What failed?                             workflow_steps[*].error + workflow_events
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from sable_platform.errors import SableError, INVALID_CONFIG
from sable_platform.workflows.models import StepDefinition, StepResult, WorkflowDefinition
from sable_platform.workflows import registry


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------

def _validate_prospect(ctx) -> StepResult:
    """Validate the prospect YAML and confirm org_id exists in DB."""
    import yaml

    yaml_path = ctx.config.get("prospect_yaml_path")
    if not yaml_path:
        raise SableError(INVALID_CONFIG, "prospect_yaml_path is required in workflow config")

    p = Path(yaml_path)
    if not p.exists():
        raise SableError(INVALID_CONFIG, f"Prospect YAML not found: {p}")

    with p.open() as f:
        prospect = yaml.safe_load(f)

    if not prospect.get("name") and not prospect.get("project_slug"):
        raise SableError(INVALID_CONFIG, "Prospect YAML must have 'name' or 'project_slug'")

    org_id = ctx.org_id
    row = ctx.db.execute("SELECT 1 FROM orgs WHERE org_id=?", (org_id,)).fetchone()
    if not row:
        raise SableError(INVALID_CONFIG, f"Org '{org_id}' not found in DB")

    return StepResult(
        status="completed",
        output={
            "prospect_yaml_path": str(p),
            "project_name": prospect.get("name") or prospect.get("project_slug", ""),
            "twitter_handle": prospect.get("twitter_handle", ""),
            "sable_org": prospect.get("sable_org", ""),
        },
    )


def _request_diagnostic(ctx) -> StepResult:
    """Invoke Cult Grader adapter to run the diagnostic."""
    from sable_platform.adapters.cult_grader import CultGraderAdapter

    adapter = CultGraderAdapter()
    result = adapter.run({
        "org_id": ctx.org_id,
        "prospect_yaml_path": ctx.input_data.get("prospect_yaml_path", ctx.config.get("prospect_yaml_path")),
        "project_name": ctx.input_data.get("project_name", ""),
        "twitter_handle": ctx.input_data.get("twitter_handle", ""),
        "sable_org": ctx.input_data.get("sable_org", ""),
    })

    return StepResult(
        status="completed",
        output={
            "diagnostic_job_ref": result.get("job_ref", ""),
            "checkpoint_path": result.get("checkpoint_path", ""),
            "diagnostic_fit_score": result.get("fit_score"),
            "diagnostic_recommended_action": result.get("recommended_action"),
        },
    )


def _poll_diagnostic(ctx) -> StepResult:
    """Check that the diagnostic completed (run_meta.json exists at checkpoint_path)."""
    from sable_platform.adapters.cult_grader import CultGraderAdapter

    checkpoint_path = ctx.input_data.get("checkpoint_path", "")
    if not checkpoint_path:
        raise SableError(INVALID_CONFIG, "checkpoint_path not set by request_diagnostic step")

    adapter = CultGraderAdapter()
    status = adapter.status(checkpoint_path)

    if status == "completed":
        result = adapter.get_result(checkpoint_path)
        run_meta = result.get("run_meta", {})
        return StepResult(
            status="completed",
            output={
                "diagnostic_status": "completed",
                "cult_run_id": run_meta.get("run_id", ""),
                "diagnostic_grade": run_meta.get("overall_grade", ""),
                "diagnostic_verdict": run_meta.get("sable_verdict", ""),
            },
        )

    # Not done yet — raise so the engine records a failure and operator can resume
    raise SableError(INVALID_CONFIG, f"Diagnostic not yet complete at {checkpoint_path}. Resume after CultGrader finishes.")


def _verify_entity_sync(ctx) -> StepResult:
    """Confirm that platform_sync wrote entities for this org after the diagnostic."""
    cult_run_id = ctx.input_data.get("cult_run_id", "")

    if cult_run_id:
        row = ctx.db.execute(
            "SELECT run_id, completed_at, overall_grade FROM diagnostic_runs WHERE cult_run_id=? AND status='completed'",
            (cult_run_id,),
        ).fetchone()
        if not row:
            raise SableError(INVALID_CONFIG, f"No completed diagnostic_run found for cult_run_id={cult_run_id}")
        diag_row_id = row["run_id"]
    else:
        diag_row_id = None

    entity_count = ctx.db.execute(
        "SELECT COUNT(*) FROM entities WHERE org_id=?",
        (ctx.org_id,),
    ).fetchone()[0]

    return StepResult(
        status="completed",
        output={
            "entity_count": entity_count,
            "diagnostic_run_id": diag_row_id,
        },
    )


def _register_artifacts(ctx) -> StepResult:
    """Register checkpoint artifacts in the artifacts table."""
    checkpoint_path = ctx.input_data.get("checkpoint_path", "")
    artifact_ids: list[int] = []

    if checkpoint_path:
        checkpoint = Path(checkpoint_path)
        for fname in ["report_internal.md", "report_outreach.md", "report_executive.md", "report_card.md"]:
            fpath = checkpoint / fname
            if fpath.exists():
                cur = ctx.db.execute(
                    """
                    INSERT INTO artifacts (org_id, artifact_type, path, metadata_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (ctx.org_id, "cult_doctor_report", str(fpath), json.dumps({"source": "prospect_diagnostic_sync", "run_id": ctx.run_id})),
                )
                artifact_ids.append(cur.lastrowid)
        ctx.db.commit()

    return StepResult(
        status="completed",
        output={"artifact_ids": artifact_ids, "artifact_count": len(artifact_ids)},
    )


def _mark_complete(ctx) -> StepResult:
    """Return a summary of the completed workflow run."""
    return StepResult(
        status="completed",
        output={
            "summary": {
                "run_id": ctx.run_id,
                "org_id": ctx.org_id,
                "entity_count": ctx.input_data.get("entity_count", 0),
                "artifact_count": ctx.input_data.get("artifact_count", 0),
                "checkpoint_path": ctx.input_data.get("checkpoint_path", ""),
            }
        },
    )


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------

PROSPECT_DIAGNOSTIC_SYNC = WorkflowDefinition(
    name="prospect_diagnostic_sync",
    version="1.0",
    steps=[
        StepDefinition(name="validate_prospect", fn=_validate_prospect, max_retries=0),
        StepDefinition(name="request_diagnostic", fn=_request_diagnostic, max_retries=1),
        StepDefinition(name="poll_diagnostic", fn=_poll_diagnostic, max_retries=2),
        StepDefinition(name="verify_entity_sync", fn=_verify_entity_sync, max_retries=1),
        StepDefinition(name="register_artifacts", fn=_register_artifacts, max_retries=1),
        StepDefinition(name="mark_complete", fn=_mark_complete, max_retries=0),
    ],
)

registry.register(PROSPECT_DIAGNOSTIC_SYNC)
