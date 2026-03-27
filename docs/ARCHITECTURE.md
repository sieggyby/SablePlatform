# Architecture

## Overview

SablePlatform is a thin coordination layer. It does not rewrite the business logic of any existing repo. It:

1. **Extracts and owns** the shared platform DB layer (previously in Sable_Slopper)
2. **Defines canonical contracts** (Pydantic models) for cross-suite data objects
3. **Provides a deterministic workflow engine** for cross-repo coordination
4. **Wraps each existing repo** in a subprocess adapter with a clean interface

## Module map

```
sable_platform/
├── errors.py               SableError + all error codes (verbatim from Slopper + 2 new)
├── contracts/
│   ├── entities.py         Entity, EntityHandle, EntityTag
│   ├── leads.py            Lead, ProspectHandoff
│   ├── diagnostics.py      DiagnosticRun
│   ├── content.py          ContentItem
│   ├── artifacts.py        Artifact
│   ├── sync.py             SyncRun
│   ├── workflows.py        WorkflowRun, WorkflowStep, WorkflowEvent
│   └── tasks.py            Task, Outcome, Recommendation
├── db/
│   ├── connection.py       get_db(), ensure_schema() — uses importlib.resources for migrations
│   ├── migrations/         001-013 SQL files (001-005 = verbatim Slopper, 006-013 = new)
│   ├── entities.py         Entity CRUD
│   ├── tags.py             Tag management (replace-current vs append semantics)
│   ├── merge.py            Merge candidates + 9-step atomic merge
│   ├── jobs.py             Job/step lifecycle (Slopper-internal use)
│   ├── cost.py             Cost logging + budget enforcement
│   ├── stale.py            mark_artifacts_stale()
│   ├── alerts.py           Alert CRUD + get_last_delivered_at / mark_delivered / mark_delivery_failed
│   └── workflow_store.py   CRUD for workflow_runs/steps/events
├── workflows/
│   ├── models.py           StepDefinition, WorkflowDefinition, StepContext, StepResult
│   ├── engine.py           WorkflowRunner (synchronous, deterministic)
│   ├── registry.py         register() / get() / list_all()
│   ├── alert_evaluator.py  evaluate_alerts() — thin orchestrator, calls all checks
│   ├── alert_checks.py     9 _check_* condition functions
│   ├── alert_delivery.py   _deliver() + _send_telegram() + _send_discord() — HTTP delivery + cooldown
│   └── builtins/
│       ├── prospect_diagnostic_sync.py   Workflow 1
│       ├── weekly_client_loop.py         Workflow 2
│       ├── alert_check.py                Workflow 3
│       ├── lead_discovery.py             Workflow 4
│       └── onboard_client.py             Workflow 5
├── adapters/
│   ├── base.py             AdapterBase Protocol + SubprocessAdapterMixin
│   ├── cult_grader.py      CultGraderAdapter
│   ├── tracking_sync.py    SableTrackingAdapter
│   ├── slopper.py          SlopperAdvisoryAdapter
│   └── lead_identifier.py  LeadIdentifierAdapter
└── cli/
    ├── main.py             sable-platform entry point; init command
    ├── workflow_cmds.py    run / resume / cancel / status / list / events / gc
    ├── inspect_cmds.py     orgs / entities / artifacts / freshness / health
    ├── alert_cmds.py       list / acknowledge / evaluate / mute / unmute / config
    ├── action_cmds.py      actions surface
    ├── journey_cmds.py     journey surface
    ├── outcome_cmds.py     outcomes surface
    └── org_cmds.py         org surface
```

## DB schema ownership

`sable_platform` owns `get_db()` and all migrations. The DB file stays at `~/.sable/sable.db` (or `SABLE_DB_PATH`). Migration path resolution uses `importlib.resources` so the package works from any install location.

**Three separate SQLite databases exist in the suite — only sable.db is owned here:**
- `~/.sable/sable.db` — platform cross-tool store (owned by SablePlatform)
- `pulse.db` / `meta.db` — Slopper-internal, not touched here
- `sable_cache.db` — Lead Identifier enrichment cache, not touched here

**Schema versioning:** `schema_version` table holds a single integer. Migration 006 adds `workflow_runs`, `workflow_steps`, `workflow_events`. Migrations 007–013 add alerts, alert_configs, discord_pulse_runs, and additional columns (cooldown_hours, last_delivered_at, step_fingerprint, last_delivery_error). Current schema head: version 13.

## Workflow engine design

The engine is **synchronous and blocking**. No threading, no asyncio.

```
WorkflowDefinition
  steps: list[StepDefinition]

StepDefinition
  name: str
  fn: Callable[[StepContext], StepResult]
  max_retries: int = 1
  skip_if: Callable[[StepContext], bool] | None

StepContext
  run_id, step_id, org_id, step_name, step_index
  input_data: dict      # merged: config + all prior step outputs
  db: sqlite3.Connection
  config: dict          # original config unchanged

StepResult
  status: "completed" | "failed" | "skipped"
  output: dict
  error: str | None
```

**Execution loop:**
1. Validate org exists
2. Create `workflow_run` row (status=running)
3. For each step:
   a. Create `workflow_step` row
   b. Evaluate `skip_if` — if true, mark skipped and continue
   c. Execute step function with retry
   d. On failure: mark run failed, raise `SableError(STEP_EXECUTION_ERROR)`
   e. Merge step output into `accumulated` dict for next step

**Resume:** Load completed/skipped steps from DB, rebuild `accumulated`, execute from first non-complete step.

**Why synchronous:** CultGrader runs take 5–20 minutes. There is no background poller. `poll_diagnostic` step fails if CultGrader isn't done yet. Operator runs `sable-platform workflow resume <run_id>` when ready. This mirrors the existing `sable resume` pattern in Slopper.

## Adapter design

All adapters use subprocess invocation as the boundary. Each adapter reads its repo path from an env var.

```
AdapterBase (Protocol)
  run(input_data: dict) -> dict           # {"status": "submitted", "job_ref": str, ...}
  status(job_ref: str) -> str            # "pending|running|completed|failed"
  get_result(job_ref: str) -> dict

SubprocessAdapterMixin
  _run_subprocess(cmd, cwd, timeout) -> CompletedProcess
  # Raises SableError(STEP_EXECUTION_ERROR) on timeout or non-zero exit
```

**Why subprocess:** No existing repo has a stable importable library API. Subprocess keeps the dependency boundary clean. When a repo matures to having a clean public API, the adapter can be upgraded internally without changing workflow definitions.

## Compatibility shims

After installing SablePlatform, Sable_Slopper's `sable/platform/*.py` files become thin re-exports:

```python
# sable/platform/db.py
from sable_platform.db.connection import get_db, ensure_schema  # noqa: F401
```

This preserves all existing import paths across the suite while moving ownership to SablePlatform.

## Jobs vs workflow tables

Two separate patterns coexist — intentionally:

| Table | Owner | Purpose |
|-------|-------|---------|
| `jobs` / `job_steps` | Slopper-internal | Onboarding orchestrator, advise pipeline |
| `workflow_runs` / `workflow_steps` / `workflow_events` | SablePlatform | Cross-suite coordination |

The `WorkflowRunner` uses the new workflow tables. The existing `sable resume` command uses `jobs`. They share the same DB but have distinct semantics.
