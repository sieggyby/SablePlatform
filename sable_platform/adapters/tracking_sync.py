"""Adapter for SableTracking platform sync."""
from __future__ import annotations

import logging
import sys
from typing import Literal

from sable_platform.adapters.base import SubprocessAdapterMixin
from sable_platform.contracts.sync import SyncRun
from sable_platform.db.connection import get_db
from sable_platform.errors import SableError, INVALID_CONFIG, STEP_EXECUTION_ERROR

log = logging.getLogger(__name__)


class SableTrackingAdapter(SubprocessAdapterMixin):
    name = "sable_tracking"

    def _repo_path(self):
        return self._resolve_repo_path("SABLE_TRACKING_PATH")

    def run(self, input_data: dict) -> dict:
        """Trigger tracking sync for an org. Blocks until completion."""
        org_id = input_data.get("org_id") or input_data.get("sable_org")
        if not org_id:
            raise SableError(INVALID_CONFIG, "org_id is required for SableTrackingAdapter.run()")

        repo = self._repo_path()
        self._run_subprocess(
            [sys.executable, "-m", "app.platform_sync_runner", org_id],
            cwd=repo,
            timeout=600,
        )
        return {"status": "completed", "job_ref": org_id, "org_id": org_id}

    def status(self, job_ref: str, conn=None) -> Literal["pending", "running", "completed", "failed"]:
        """job_ref is org_id for tracking adapter; check sync_runs table."""
        _owns = conn is None
        conn = conn or get_db()
        try:
            row = conn.execute(
                """
                SELECT status FROM sync_runs
                WHERE org_id=? AND sync_type='sable_tracking'
                ORDER BY started_at DESC LIMIT 1
                """,
                (job_ref,),
            ).fetchone()
            if row is None:
                return "pending"
            s = row["status"]
            if s == "completed":
                return "completed"
            if s in ("failed", "error"):
                return "failed"
            return "running"
        finally:
            if _owns:
                conn.close()

    def get_result(self, job_ref: str, conn=None) -> dict:
        """Read latest tracking sync run for org.

        Validates the sync run row against the SyncRun contract.
        Raises SableError if the row is malformed.
        """
        _owns = conn is None
        conn = conn or get_db()
        try:
            row = conn.execute(
                """
                SELECT * FROM sync_runs
                WHERE org_id=? AND sync_type='sable_tracking'
                ORDER BY started_at DESC LIMIT 1
                """,
                (job_ref,),
            ).fetchone()
            if row is None:
                return {}
            row_dict = dict(row)
            try:
                SyncRun.model_validate(row_dict)
            except Exception as e:
                raise SableError(
                    STEP_EXECUTION_ERROR,
                    f"SableTracking sync run validation failed for org '{job_ref}': {e}",
                ) from e
            return row_dict
        finally:
            if _owns:
                conn.close()
