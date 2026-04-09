"""Deterministic, synchronous workflow runner."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time

from sqlalchemy.exc import IntegrityError as SAIntegrityError

from sable_platform.db.compat import get_dialect, hours_since
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
from sable_platform.errors import (
    SableError, STEP_EXECUTION_ERROR, WORKFLOW_ALREADY_RUNNING, WORKFLOW_NOT_FOUND, redact_error,
)
from sable_platform.workflows.models import (
    StepContext,
    StepDefinition,
    StepResult,
    WorkflowDefinition,
)

logger = logging.getLogger(__name__)

# Stale lock threshold: if an in_progress run is older than this, auto-fail it
_STALE_LOCK_HOURS = 4

_WORKFLOW_FINGERPRINT_VERSION = "v2"


def _is_active_run_lock_error(exc: sqlite3.IntegrityError) -> bool:
    msg = str(exc)
    return (
        "idx_workflow_runs_active_lock" in msg
        or "workflow_runs.org_id, workflow_runs.workflow_name" in msg
    )


def _workflow_fingerprint(workflow_def: WorkflowDefinition) -> str:
    """Compute a versioned fingerprint of the workflow's ordered step names."""
    names = "|".join(s.name for s in workflow_def.steps)
    digest = hashlib.sha1(names.encode()).hexdigest()[:8]
    return f"{_WORKFLOW_FINGERPRINT_VERSION}:{digest}"


def _legacy_workflow_fingerprint(workflow_def: WorkflowDefinition) -> str:
    """Reproduce the pre-v2 fingerprint that ignored step order."""
    names = "|".join(sorted(s.name for s in workflow_def.steps))
    return hashlib.sha1(names.encode()).hexdigest()[:8]


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

            # --- Execution locking (SP-LOCK) ---
            self._check_execution_lock(conn, org_id)

            fp = _workflow_fingerprint(self.definition)
            run_id = create_workflow_run(
                conn,
                org_id=org_id,
                workflow_name=self.definition.name,
                workflow_version=self.definition.version,
                config=config,
                step_fingerprint=fp,
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
        ignore_version_check: bool = False,
    ) -> str:
        """Resume a failed or interrupted run from the first non-completed step.

        Returns run_id.
        Raises SableError(STEP_EXECUTION_ERROR) if the workflow definition changed
        since the run was created, unless ignore_version_check=True.
        """
        _owns_conn = conn is None
        conn = conn or get_db()
        try:
            run_row = get_workflow_run(conn, run_id)
            if run_row is None:
                raise SableError(WORKFLOW_NOT_FOUND, f"Workflow run '{run_id}' not found")
            if run_row["status"] in ("completed", "cancelled"):
                raise SableError(WORKFLOW_NOT_FOUND, f"Workflow run '{run_id}' is already {run_row['status']}")

            # Check for OTHER active runs on the same (org, workflow) — exclude
            # the run being resumed since it will transition from failed→running.
            org_id = run_row["org_id"]
            other_active = conn.execute(
                """
                SELECT run_id FROM workflow_runs
                WHERE org_id=? AND workflow_name=? AND status IN ('pending', 'running')
                  AND run_id != ?
                LIMIT 1
                """,
                (org_id, run_row["workflow_name"], run_id),
            ).fetchone()
            if other_active:
                raise SableError(
                    WORKFLOW_ALREADY_RUNNING,
                    f"Cannot resume: another run ({other_active['run_id']}) "
                    f"is already active for '{run_row['workflow_name']}' on org '{org_id}'.",
                )

            # Workflow config versioning check
            if not ignore_version_check:
                stored_fp = run_row["step_fingerprint"]
                if stored_fp is not None:
                    current_fp = _workflow_fingerprint(self.definition)
                    if stored_fp.startswith(f"{_WORKFLOW_FINGERPRINT_VERSION}:"):
                        fingerprint_matches = stored_fp == current_fp
                    else:
                        fingerprint_matches = stored_fp == _legacy_workflow_fingerprint(self.definition)
                    if not fingerprint_matches:
                        raise SableError(
                            STEP_EXECUTION_ERROR,
                            f"Workflow definition changed since run was created "
                            f"(stored={stored_fp}, current={current_fp}). "
                            f"Use --ignore-version-check to resume anyway.",
                        )

            # Rebuild accumulated output from all completed/skipped steps
            step_rows = get_workflow_steps(conn, run_id)
            org_id = run_row["org_id"]
            config = json.loads(run_row["config_json"] or "{}")
            accumulated = dict(config)

            # C1: Reset any steps left in 'running' state from a prior crash.
            # Without this they would be re-executed, creating orphaned rows.
            for step_row in step_rows:
                if step_row["status"] == "running":
                    fail_workflow_step(
                        conn, step_row["step_id"],
                        "auto-failed during resume: was in running state at resume time",
                    )
                    logger.warning(
                        "Auto-failed orphaned running step %s (%s) during resume of run %s",
                        step_row["step_name"], step_row["step_id"], run_id,
                        extra={"run_id": run_id, "org_id": org_id, "step_name": step_row["step_name"]},
                    )

            completed_names: set[str] = set()
            for step_row in step_rows:
                if step_row["status"] in ("completed", "skipped"):
                    completed_names.add(step_row["step_name"])
                    if step_row["output_json"]:
                        # D2: Guard against corrupted output_json — skip the
                        # update for that step and continue rather than crash.
                        try:
                            accumulated.update(json.loads(step_row["output_json"]))
                        except json.JSONDecodeError:
                            logger.warning(
                                "Skipping corrupt output_json for step %s (%s) during resume of run %s",
                                step_row["step_name"], step_row["step_id"], run_id,
                                extra={"run_id": run_id, "org_id": org_id, "step_name": step_row["step_name"]},
                            )

            # Reset run to running
            try:
                conn.execute(
                    "UPDATE workflow_runs SET status='running', error=NULL WHERE run_id=?",
                    (run_id,),
                )
                conn.commit()
            except (sqlite3.IntegrityError, SAIntegrityError) as exc:
                if _is_active_run_lock_error(exc):
                    raise SableError(
                        WORKFLOW_ALREADY_RUNNING,
                        f"Cannot resume: another active run exists for '{run_row['workflow_name']}' on org '{org_id}'.",
                    ) from exc
                raise
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
    # Execution locking
    # ------------------------------------------------------------------

    def _check_execution_lock(self, conn: sqlite3.Connection, org_id: str) -> None:
        """Prevent concurrent runs of the same workflow on the same org.

        If an existing in_progress run is found:
        - If older than _STALE_LOCK_HOURS, auto-fail it (stale-lock recovery)
        - Otherwise, raise WORKFLOW_ALREADY_RUNNING
        """
        row = conn.execute(
            """
            SELECT run_id, started_at, created_at FROM workflow_runs
            WHERE org_id=? AND workflow_name=? AND status IN ('pending', 'running')
            ORDER BY created_at DESC LIMIT 1
            """,
            (org_id, self.definition.name),
        ).fetchone()

        if row is None:
            return

        # Use started_at for running runs, created_at for pending (never-started) runs
        age_reference = row["started_at"] or row["created_at"]
        if age_reference:
            _dialect = get_dialect(conn)
            _expr = hours_since(":ts", _dialect)
            age = conn.execute(
                f"SELECT {_expr} AS hours_old",
                {"ts": age_reference},
            ).fetchone()
            if age and age["hours_old"] is not None and age["hours_old"] >= _STALE_LOCK_HOURS:
                # Auto-fail stale run
                fail_workflow_run(conn, row["run_id"], "auto-failed: stale lock recovery")
                emit_workflow_event(
                    conn, row["run_id"], "run_failed", None,
                    {"reason": "stale_lock_recovery", "age_hours": round(age["hours_old"], 1)},
                )
                logger.warning(
                    "Auto-failed stale run %s (%.1fh old) for %s/%s",
                    row["run_id"], age["hours_old"], org_id, self.definition.name,
                    extra={"run_id": row["run_id"], "org_id": org_id},
                )
                return

        raise SableError(
            WORKFLOW_ALREADY_RUNNING,
            f"Workflow '{self.definition.name}' already has an active run "
            f"({row['run_id']}) for org '{org_id}'. "
            f"Use 'sable-platform workflow unlock {row['run_id']}' to force-fail it.",
        )

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
                    logger.warning(
                        "skip_if raised for step %s: %s", step_def.name, exc,
                        extra={"run_id": run_id, "org_id": org_id, "step_name": step_def.name},
                    )
                    should_skip = False

                if should_skip:
                    skip_workflow_step(conn, step_id, "skip_if condition met")
                    emit_workflow_event(conn, run_id, "step_skipped", step_id, {"step": step_def.name})
                    logger.info(
                        "Skipped step %s (skip_if)", step_def.name,
                        extra={"run_id": run_id, "org_id": org_id, "step_name": step_def.name},
                    )
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
        logger.info(
            "Workflow '%s' run %s completed", self.definition.name, run_id,
            extra={"run_id": run_id, "org_id": org_id},
        )

    @staticmethod
    def _run_with_timeout(step_def: StepDefinition, ctx: StepContext) -> StepResult:
        """Execute step function, enforcing timeout_seconds if set."""
        if step_def.timeout_seconds is None:
            return step_def.fn(ctx)

        result_holder: list[StepResult] = []
        error_holder: list[Exception] = []

        def _target() -> None:
            try:
                result_holder.append(step_def.fn(ctx))
            except Exception as exc:
                error_holder.append(exc)

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        thread.join(timeout=step_def.timeout_seconds)

        if thread.is_alive():
            # Step didn't finish in time — thread is abandoned (daemon)
            logger.warning(
                "Step %s timed out after %ds", step_def.name, step_def.timeout_seconds,
                extra={"step_name": step_def.name},
            )
            return StepResult(status="failed", output={}, error="step_timeout")

        if error_holder:
            raise error_holder[0]

        return result_holder[0]

    def _execute_with_retry(
        self,
        step_def: StepDefinition,
        ctx: StepContext,
        conn: sqlite3.Connection,
    ) -> StepResult:
        last_error: str = ""
        failed_output: dict = {}

        for attempt in range(step_def.max_retries + 1):
            start_workflow_step(conn, ctx.step_id)
            emit_workflow_event(
                conn, ctx.run_id, "step_started", ctx.step_id,
                {"step": step_def.name, "attempt": attempt},
            )
            try:
                result = self._run_with_timeout(step_def, ctx)
                if result.status == "completed":
                    complete_workflow_step(conn, ctx.step_id, result.output)
                    emit_workflow_event(
                        conn, ctx.run_id, "step_completed", ctx.step_id,
                        {"step": step_def.name},
                    )
                    logger.info(
                        "Step %s completed (attempt %d)", step_def.name, attempt,
                        extra={"run_id": ctx.run_id, "org_id": ctx.org_id, "step_name": step_def.name},
                    )
                    return result

                if result.status == "skipped":
                    skip_reason = result.error or "step returned skipped status"
                    skip_workflow_step(conn, ctx.step_id, skip_reason)
                    emit_workflow_event(
                        conn, ctx.run_id, "step_skipped", ctx.step_id,
                        {"step": step_def.name},
                    )
                    logger.info(
                        "Step %s skipped (attempt %d)", step_def.name, attempt,
                        extra={"run_id": ctx.run_id, "org_id": ctx.org_id, "step_name": step_def.name},
                    )
                    return result

                failed_output = dict(result.output or {})
                last_error = redact_error(
                    result.error or f"Step '{step_def.name}' returned failed status"
                )

            except Exception as exc:
                last_error = redact_error(str(exc))
                failed_output = {}

                fail_workflow_step(conn, ctx.step_id, last_error)
                emit_workflow_event(
                    conn, ctx.run_id, "step_failed", ctx.step_id,
                    {"step": step_def.name, "error": last_error, "attempt": attempt},
                )
                logger.warning(
                    "Step %s failed (attempt %d): %s", step_def.name, attempt, last_error,
                    extra={"run_id": ctx.run_id, "org_id": ctx.org_id, "step_name": step_def.name},
                )

            else:
                fail_workflow_step(conn, ctx.step_id, last_error)
                emit_workflow_event(
                    conn, ctx.run_id, "step_failed", ctx.step_id,
                    {"step": step_def.name, "error": last_error, "attempt": attempt},
                )
                logger.warning(
                    "Step %s failed (attempt %d): %s", step_def.name, attempt, last_error,
                    extra={"run_id": ctx.run_id, "org_id": ctx.org_id, "step_name": step_def.name},
                )

            if attempt < step_def.max_retries:
                reset_workflow_step_for_retry(conn, ctx.step_id)
                if step_def.retry_delay_seconds > 0:
                    time.sleep(step_def.retry_delay_seconds)

        return StepResult(status="failed", output=failed_output, error=last_error)
