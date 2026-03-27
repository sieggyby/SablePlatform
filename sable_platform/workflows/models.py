"""Workflow engine data types (pure Python dataclasses)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional


@dataclass
class StepResult:
    status: Literal["completed", "failed", "skipped"]
    output: dict
    error: Optional[str] = None


@dataclass
class StepContext:
    run_id: str
    step_id: str
    org_id: str
    step_name: str
    step_index: int
    input_data: dict          # merged: original config + all prior step outputs
    db: sqlite3.Connection
    config: dict              # original workflow config unchanged


@dataclass
class StepDefinition:
    name: str
    fn: Callable[[StepContext], StepResult]
    max_retries: int = 1
    retry_delay_seconds: float = 0.0
    skip_if: Optional[Callable[[StepContext], bool]] = None


@dataclass
class WorkflowDefinition:
    name: str
    version: str
    steps: list[StepDefinition]
