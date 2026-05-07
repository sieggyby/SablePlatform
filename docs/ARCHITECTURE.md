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
├── errors.py               SableError + all error codes
├── cron.py                 Crontab scheduler — add/remove/list/presets
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
│   ├── connection.py       get_db(), ensure_schema() — importlib.resources migrations
│   ├── backup.py           SQLite online backup — WAL-safe, atomic, pruning
│   ├── migrations/         001–030 SQL files
│   ├── entities.py         Entity CRUD + entity_notes
│   ├── tags.py             Tag management (replace-current vs append semantics)
│   ├── merge.py            Merge candidates + 9-step atomic merge
│   ├── jobs.py             Job/step lifecycle (Slopper-internal use)
│   ├── cost.py             Cost logging + budget enforcement
│   ├── stale.py            mark_artifacts_stale()
│   ├── alerts.py           Alert CRUD + delivery tracking
│   ├── interactions.py     Interaction edge CRUD (relationship web)
│   ├── decay.py            Decay score CRUD (churn prediction)
│   ├── centrality.py       Centrality score CRUD (bridge nodes)
│   ├── prospects.py        Prospect score CRUD (Lead Identifier)
│   ├── playbook.py         Playbook target/outcome CRUD
│   ├── discord_pulse.py    Discord pulse run CRUD
│   ├── watchlist.py        Watchlist + snapshot-based change detection
│   ├── audit.py            Append-only operator audit log
│   ├── webhooks.py         Webhook subscription CRUD (SSRF-hardened)
│   └── workflow_store.py   CRUD for workflow_runs/steps/events
├── workflows/
│   ├── models.py           StepDefinition, WorkflowDefinition, StepContext, StepResult
│   ├── engine.py           WorkflowRunner (synchronous, deterministic)
│   ├── registry.py         register() / get() / list_all()
│   ├── alert_evaluator.py  evaluate_alerts() — thin orchestrator, per-org isolation
│   ├── alert_checks.py     12 _check_* condition functions
│   ├── alert_delivery.py   deliver_alerts_by_ids() + _deliver() + _send_telegram() + _send_discord()
│   └── builtins/
│       ├── prospect_diagnostic_sync.py   Workflow 1
│       ├── weekly_client_loop.py         Workflow 2
│       ├── alert_check.py                Workflow 3
│       ├── lead_discovery.py             Workflow 4
│       └── onboard_client.py             Workflow 5
├── webhooks/
│   └── dispatch.py         HMAC-SHA256 webhook dispatch
├── http_health.py          Bearer-authenticated /health HTTP server (serve_health())
├── metrics.py              Prometheus text format export (export_metrics())
├── adapters/
│   ├── base.py             AdapterBase Protocol + SubprocessAdapterMixin
│   ├── cult_grader.py      CultGraderAdapter
│   ├── tracking_sync.py    SableTrackingAdapter
│   ├── slopper.py          SlopperAdvisoryAdapter (handle resolution via entity_handles)
│   └── lead_identifier.py  LeadIdentifierAdapter
└── cli/
    ├── main.py             sable-platform entry point; init, backup, schema, gc, health-server, metrics
    ├── workflow_cmds.py    run / resume / cancel / status / list / events / gc / preflight
    ├── inspect_cmds.py     orgs / entities / artifacts / freshness / health / interactions / decay / centrality / spend / audit / playbook / prospects
    ├── alert_cmds.py       list / acknowledge / evaluate / mute / unmute / config
    ├── action_cmds.py      actions surface
    ├── dashboard_cmds.py   urgency-sorted operator dashboard
    ├── journey_cmds.py     entity lifecycle timeline + funnel
    ├── outcome_cmds.py     outcomes surface
    ├── org_cmds.py         org create / list / graduate / reject
    ├── watchlist_cmds.py   add / remove / list / changes / snapshot
    ├── webhook_cmds.py     add / list / remove / test
    └── cron_cmds.py        add / remove / list / presets
```

## DB schema ownership

`sable_platform` owns `get_db()` and all 39 migrations. Runtime DB resolution is `SABLE_DATABASE_URL` first, then SQLite at `~/.sable/sable.db` (or `SABLE_DB_PATH`). Migration path resolution uses packaged Alembic assets via `importlib.resources`, so the Postgres migration path works from wheels and containers as well as source checkouts. SQLite connections still set `PRAGMA busy_timeout=5000` for concurrent access reliability.

**Three separate suite databases exist — only sable.db is owned here:**
- `~/.sable/sable.db` (SQLite fallback) or `SABLE_DATABASE_URL` (runtime target) — platform cross-tool store (owned by SablePlatform)
- `pulse.db` / `meta.db` — Slopper-internal, not touched here
- `sable_cache.db` — Lead Identifier enrichment cache, not touched here

**Schema versioning:** `schema_version` table holds a single integer. Migrations are append-only and idempotent. Current schema head: **version 39**.

## Workflow engine design

The engine is **synchronous and blocking**. Threading is used only for per-step timeouts.

```
WorkflowDefinition
  steps: list[StepDefinition]

StepDefinition
  name: str
  fn: Callable[[StepContext], StepResult]
  max_retries: int = 1
  retry_delay_seconds: float = 0.0
  skip_if: Callable[[StepContext], bool] | None
  timeout_seconds: int | None     # None = no timeout

StepContext
  run_id, step_id, org_id, step_name, step_index
  input_data: dict      # merged: config + all prior step outputs
  db: CompatConnection   # works with SQLite and Postgres (use text() + :named params)
  config: dict          # original config unchanged

StepResult
  status: "completed" | "failed" | "skipped"
  output: dict
  error: str | None
```

**Execution loop:**
1. Validate org exists
2. Create `workflow_run` row (status=running, step_fingerprint=sha1[:8])
3. For each step:
   a. Create `workflow_step` row
   b. Evaluate `skip_if` — if true, mark skipped and continue
   c. Execute step function with retry (up to `max_retries`)
   d. On failure: mark run failed, raise `SableError(STEP_EXECUTION_ERROR)`
   e. Merge step output into `accumulated` dict for next step

**Resume:** Load completed/skipped steps from DB, rebuild `accumulated`, execute from first non-complete step. Validates step_fingerprint matches current definition (blocks on mismatch, `--ignore-version-check` to bypass).

**Why synchronous:** CultGrader runs take 5–20 minutes. There is no background poller. `poll_diagnostic` step fails if CultGrader isn't done yet. Operator runs `sable-platform workflow resume <run_id>` when ready.

See `docs/EXTENDING.md` § Adding a Workflow for step-by-step instructions.

## Adapter design

All adapters use subprocess invocation as the boundary. Each adapter reads its repo path from an env var.

```
AdapterBase (Protocol)
  run(input_data: dict) -> dict           # {"status": "submitted", "job_ref": str, ...}
  status(job_ref: str) -> str            # "pending|running|completed|failed"
  get_result(job_ref: str) -> dict

SubprocessAdapterMixin
  _run_subprocess(cmd, cwd, timeout) -> CompletedProcess
  _resolve_repo_path(env_var) -> str
  # Raises SableError(STEP_EXECUTION_ERROR) on timeout or non-zero exit
```

**SlopperAdvisoryAdapter note:** Resolves `org_id` to primary Twitter handle via `entity_handles` before passing to `sable advise`. Falls back to any non-archived Twitter handle if no primary. Raises `SableError(INVALID_CONFIG)` if no handle found.

**Why subprocess:** No existing repo has a stable importable library API. Subprocess keeps the dependency boundary clean.

See `docs/EXTENDING.md` § Adding an Adapter for step-by-step instructions.

## Jobs vs workflow tables

Two separate patterns coexist — intentionally:

| Table | Owner | Purpose |
|-------|-------|---------|
| `jobs` / `job_steps` | Slopper-internal | Onboarding orchestrator, advise pipeline |
| `workflow_runs` / `workflow_steps` / `workflow_events` | SablePlatform | Cross-suite coordination |
