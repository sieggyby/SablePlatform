"""Adapter for Sable_Slopper advisory / strategy generation."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from sable_platform.adapters.base import SubprocessAdapterMixin
from sable_platform.errors import SableError, INVALID_CONFIG


class SlopperAdvisoryAdapter(SubprocessAdapterMixin):
    name = "slopper_advisory"

    def _repo_path(self) -> Path:
        env = os.environ.get("SABLE_SLOPPER_PATH")
        if not env:
            raise SableError(INVALID_CONFIG, "SABLE_SLOPPER_PATH environment variable is not set")
        p = Path(env)
        if not p.is_dir():
            raise SableError(INVALID_CONFIG, f"SABLE_SLOPPER_PATH does not exist: {p}")
        return p

    def run(self, input_data: dict) -> dict:
        """Trigger strategy/advise generation for an org. Blocks until completion."""
        org_id = input_data.get("org_id")
        if not org_id:
            raise SableError(INVALID_CONFIG, "org_id is required for SlopperAdvisoryAdapter.run()")

        repo = self._repo_path()
        self._run_subprocess(
            [sys.executable, "-m", "sable", "advise", org_id],
            cwd=repo,
            timeout=600,
        )
        return {"status": "completed", "job_ref": org_id, "org_id": org_id}

    def status(self, job_ref: str) -> Literal["pending", "running", "completed", "failed"]:
        """Check latest artifact freshness for org."""
        from sable_platform.db.connection import get_db
        conn = get_db()
        try:
            row = conn.execute(
                """
                SELECT stale FROM artifacts
                WHERE org_id=? AND artifact_type='twitter_strategy_brief'
                ORDER BY created_at DESC LIMIT 1
                """,
                (job_ref,),
            ).fetchone()
            if row is None:
                return "pending"
            return "completed" if not row["stale"] else "failed"
        finally:
            conn.close()

    def get_result(self, job_ref: str) -> dict:
        from sable_platform.db.connection import get_db
        conn = get_db()
        try:
            rows = conn.execute(
                """
                SELECT * FROM artifacts
                WHERE org_id=? AND stale=0
                ORDER BY created_at DESC LIMIT 5
                """,
                (job_ref,),
            ).fetchall()
            return {"artifacts": [dict(r) for r in rows]}
        finally:
            conn.close()
