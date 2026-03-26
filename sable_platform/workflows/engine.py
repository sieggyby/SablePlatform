"""Deterministic, synchronous workflow runner."""
from __future__ import annotations

import json
import logging
import sqlite3

from sable_platform.db.connection import get_db
from sable_platform.db.workflow_store import (
    complete_workflow_run,
    complete_workflow_step,
    create_workflow_run,
    create_workflow_step,
    emit_workflow_event,
    fail_workflow_run,
    fail_workflow_step,
    get_workflow_run,
    get_workflow_steps,
    reset_workflow_step_for_retry,
    skip_workflow_step,
    start_workflow_run,
    start_workflow_step,
)
from sable_platform.errors import SableError, STEP_EXECUTION_ERROR, WORKFLOW_NOT_FOUND, redact_error
from sable_platform.workflows.models import (
    StepContext,
    StepDefinition,
    StepResult,
    WorkflowDefinition,
)

logger = logging.getLogger(__name__)


class WorkflowRunner:
    def __init__(self, definition: WorkflowDefinition):
        self.definition = definition

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        org_id: str,
        config: dict,
        conn: sqlite3.Connection | None = None,
    ) -> str:
        """Start a new workflow run and execute to completion (or first failure).

        Returns the run_id.
        Raises SableError(STEP_EXECUTION_ERROR) on unrecoverable step failure.
        """
        _owns_conn = conn is None
        conn = conn or get_db()
        try:
            # Validate org exists before inserting workflow_run (avoids FK error)
            row = conn.execute("SELECT 1 FROM orgs WHERE org_id=?", (org_id,)).fetchone()
            if not row:
                from sable_platform.errors import ORG_NOT_FOUND
                raise SableError(ORG_NOT_FOUND, f"Org '{org_id}' not found")

            run_id = create_workflow_run(
                conn,
                org_id=org_id,
                workflow_name=self.definition.name,
                workflow_version=self.definition.version,
                config=config,
            )
            start_workflow_run(conn, run_id)
            emit_workflow_event(conn, run_id, "run_started", payload={"org_id": org_id})
            self._execute_steps(conn, run_id, org_id, config, accumulated=dict(config))
            return run_id
        finally:
            if _owns_conn:
                conn.close()

    def resume(
        self,
        run_id: str,
        conn: sqlite3.Connection | None = None,
    ) -> str:
        """Resume a failed or interrupted run from the first non-completed step.

        Returns run_id.
        """
        _owns_conn = conn is None
        conn = conn or get_db()
        try:
            run_row = get_workflow_run(conn, run_id)
            if run_row is None:
                raise SableError(WORKFLOW_NOT_FOUND, f"Workflow run '{run_id}' not found")
            if run_row["status"] == "completed":
                raise SableError(WORKFLOW_NOT_FOUND, f"Workflow run '{run_id}' is already completed")

            # Rebuild accumulated output from all completed/skipped steps
            step_rows = get_workflow_steps(conn, run_id)
            org_id = run_row["org_id"]
            config = json.loads(run_row["config_json"] or "{}")
            accumulated = dict(config)

            completed_names: set[str] = set()
            for step_row in step_rows:
                if step_row["status"] in ("completed", "skipped"):
                    completed_names.add(step_row["step_name"])
                    if step_row["output_json"]:
                        accumulated.update(json.loads(step_row["output_json"]))

            # Reset run to running
            conn.execute(
                "UPDATE workflow_runs SET status='running', error=NULL WHERE run_id=?",
                (run_id,),
            )
            conn.commit()
            emit_workflow_event(conn, run_id, "run_resumed", payload={"org_id": org_id})

            self._execute_steps(
                conn, run_id, org_id, config, accumulated,
                skip_names=completed_names,
            )
            return run_id
        finally:
            if _owns_conn:
                conn.close()

    # ------------------------------------------------------------------
    # Internal execution loop
    # ------------------------------------------------------------------

    def _execute_steps(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        org_id: str,
        config: dict,
        accumulated: dict,
        skip_names: set[str] | None = None,
    ) -> None:
        skip_names = skip_names or set()

        for index, step_def in enumerate(self.definition.steps):
            if step_def.name in skip_names:
                continue

            step_id = create_workflow_step(conn, run_id, step_def.name, index, accumulated)

            ctx = StepContext(
                run_id=run_id,
                step_id=step_id,
                org_id=org_id,
                step_name=step_def.name,
                step_index=index,
                input_data=dict(accumulated),
                db=conn,
                config=config,
            )

            # Evaluate skip_if
            if step_def.skip_if is not None:
                try:
                    should_skip = step_def.skip_if(ctx)
                except Exception as exc:
                    logger.warning("skip_if raised for step %s: %s", step_def.name, exc)
                    should_skip = False

                if should_skip:
                    skip_workflow_step(conn, step_id, "skip_if condition met")
                    emit_workflow_event(conn, run_id, "step_skipped", step_id, {"step": step_def.name})
                    logger.info("Skipped step %s (skip_if)", step_def.name)
                    continue

            # Execute with retry
            result = self._execute_with_retry(step_def, ctx, conn)

            if result.status == "failed":
                fail_workflow_run(conn, run_id, result.error or "step failed")
                emit_workflow_event(
                    conn, run_id, "run_failed", step_id,
                    {"step": step_def.name, "error": result.error},
                )
                raise SableError(
                    STEP_EXECUTION_ERROR,
                    f"Step '{step_def.name}' failed: {result.error}",
                )

            if result.output:
                accumulated.update(result.output)

        complete_workflow_run(conn, run_id)
        emit_workflow_event(conn, run_id, "run_completed", payload={"run_id": run_id})
        logger.info("Workflow '%s' run %s completed", self.definition.name, run_id)

    def _execute_with_retry(
        self,
        step_def: StepDefinition,
        ctx: StepContext,
        conn: sqlite3.Connection,
    ) -> StepResult:
        last_error: str = ""

        for attempt in range(step_def.max_retries + 1):
            start_workflow_step(conn, ctx.step_id)
            emit_workflow_event(
                conn, ctx.run_id, "step_started", ctx.step_id,
                {"step": step_def.name, "attempt": attempt},
            )
            try:
                result = step_def.fn(ctx)
                complete_workflow_step(conn, ctx.step_id, result.output)
                emit_workflow_event(
                    conn, ctx.run_id, "step_completed", ctx.step_id,
                    {"step": step_def.name},
                )
                logger.info("Step %s completed (attempt %d)", step_def.name, attempt)
                return result

            except Exception as exc:
                last_error = redact_error(str(exc))
                fail_workflow_step(conn, ctx.step_id, last_error)
                emit_workflow_event(
                    conn, ctx.run_id, "step_failed", ctx.step_id,
                    {"step": step_def.name, "error": last_error, "attempt": attempt},
                )
                logger.warning("Step %s failed (attempt %d): %s", step_def.name, attempt, last_error)

                if attempt < step_def.max_retries:
                    reset_workflow_step_for_retry(conn, ctx.step_id)

        return StepResult(status="failed", output={}, error=last_error)
