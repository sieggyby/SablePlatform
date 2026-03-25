"""Adapter for Sable_Cult_Grader."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal

from sable_platform.adapters.base import SubprocessAdapterMixin
from sable_platform.contracts.leads import ProspectHandoff
from sable_platform.errors import SableError, INVALID_CONFIG, STEP_EXECUTION_ERROR


class CultGraderAdapter(SubprocessAdapterMixin):
    name = "cult_grader"

    def _repo_path(self) -> Path:
        env = os.environ.get("SABLE_CULT_GRADER_PATH")
        if not env:
            raise SableError(
                INVALID_CONFIG,
                "SABLE_CULT_GRADER_PATH environment variable is not set",
            )
        p = Path(env)
        if not p.is_dir():
            raise SableError(INVALID_CONFIG, f"SABLE_CULT_GRADER_PATH does not exist: {p}")
        return p

    def run(self, input_data: dict) -> dict:
        """Trigger a diagnostic run. Returns {"status": "submitted", "job_ref": str, "checkpoint_path": str}."""
        handoff = ProspectHandoff.model_validate(input_data)
        repo = self._repo_path()

        self._run_subprocess(
            [sys.executable, "diagnose.py", "--config", handoff.prospect_yaml_path],
            cwd=repo,
            timeout=3600,  # CultGrader runs can take up to ~1h
        )

        # After subprocess returns, find the run_meta.json to get the run_id
        result = self._parse_latest_run(repo, handoff)
        return {"status": "submitted", "job_ref": result.get("run_id", ""), **result}

    def status(self, job_ref: str) -> Literal["pending", "running", "completed", "failed"]:
        """Check completion by looking for run_meta.json at the checkpoint path."""
        # job_ref is the checkpoint directory path for file-based status
        checkpoint = Path(job_ref)
        if not checkpoint.exists():
            return "pending"
        if (checkpoint / "run_meta.json").exists():
            return "completed"
        return "running"

    def get_result(self, job_ref: str) -> dict:
        """Read diagnostic.json and run_meta.json from checkpoint dir."""
        checkpoint = Path(job_ref)
        result: dict = {}

        diagnostic_file = checkpoint / "diagnostic.json"
        if diagnostic_file.exists():
            result["diagnostic"] = json.loads(diagnostic_file.read_text(encoding="utf-8"))

        run_meta_file = checkpoint / "run_meta.json"
        if run_meta_file.exists():
            result["run_meta"] = json.loads(run_meta_file.read_text(encoding="utf-8"))

        return result

    def _parse_latest_run(self, repo: Path, handoff: ProspectHandoff) -> dict:
        """After a completed run, find the checkpoint path from the diagnostics dir."""
        import yaml

        # Read the project slug from the prospect YAML
        prospect_file = Path(handoff.prospect_yaml_path)
        if not prospect_file.exists():
            raise SableError(INVALID_CONFIG, f"Prospect YAML not found: {prospect_file}")

        with prospect_file.open() as f:
            prospect = yaml.safe_load(f)

        slug = prospect.get("project_slug") or prospect.get("slug") or prospect.get("name", "unknown")
        diagnostics_dir = repo / "diagnostics" / slug / "runs" / "latest"

        if not diagnostics_dir.exists():
            # Try resolving symlink manually
            runs_dir = repo / "diagnostics" / slug / "runs"
            if runs_dir.exists():
                dated_runs = sorted(runs_dir.iterdir(), reverse=True)
                for d in dated_runs:
                    if d.is_dir() and d.name != "latest":
                        diagnostics_dir = d
                        break

        run_meta_path = diagnostics_dir / "run_meta.json"
        if run_meta_path.exists():
            meta = json.loads(run_meta_path.read_text(encoding="utf-8"))
            return {
                "run_id": meta.get("run_id", ""),
                "checkpoint_path": str(diagnostics_dir),
                "fit_score": meta.get("fit_score"),
                "recommended_action": meta.get("recommendation"),
            }

        return {"checkpoint_path": str(diagnostics_dir)}
