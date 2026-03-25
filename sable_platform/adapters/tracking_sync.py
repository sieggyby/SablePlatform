"""Adapter for SableTracking platform sync."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from sable_platform.adapters.base import SubprocessAdapterMixin
from sable_platform.errors import SableError, INVALID_CONFIG


class SableTrackingAdapter(SubprocessAdapterMixin):
    name = "sable_tracking"

    def _repo_path(self) -> Path:
        env = os.environ.get("SABLE_TRACKING_PATH")
        if not env:
            raise SableError(INVALID_CONFIG, "SABLE_TRACKING_PATH environment variable is not set")
        p = Path(env)
        if not p.is_dir():
            raise SableError(INVALID_CONFIG, f"SABLE_TRACKING_PATH does not exist: {p}")
        return p

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

    def status(self, job_ref: str) -> Literal["pending", "running", "completed", "failed"]:
        """job_ref is org_id for tracking adapter; check sync_runs table."""
        from sable_platform.db.connection import get_db
        conn = get_db()
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
            conn.close()

    def get_result(self, job_ref: str) -> dict:
        from sable_platform.db.connection import get_db
        conn = get_db()
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
            return dict(row)
        finally:
            conn.close()
