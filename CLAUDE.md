# CLAUDE.md — SablePlatform

## What This Is

SablePlatform is the suite-level backbone for the Sable tool stack. It owns:
- The shared `sable.db` connection factory and all migrations
- Canonical Pydantic contracts for cross-suite data objects
- A deterministic workflow engine (synchronous, durable, resumable)
- Subprocess adapters to each specialized repo
- The `sable-platform` CLI

It does NOT own the business logic of any specialized repo. Those stay in:
- `Sable_Community_Lead_Identifier` — prospect discovery
- `Sable_Cult_Grader` — diagnostic and playbook
- `SableTracking` — intake and contributor tracking
- `Sable_Slopper` — strategy, content, account ops

## Current State

**v0.3** is complete. Includes:
- DB layer (014 migrations, all helpers)
- Contracts (all cross-suite Pydantic models)
- WorkflowRunner (synchronous, deterministic, retry/resume/skip_if, config versioning)
- 5 builtin workflows (prospect_diagnostic_sync, weekly_client_loop, alert_check, lead_discovery, onboard_client)
- Subprocess adapters for all 4 repos
- CLI (workflow run/resume/cancel/status/list/events/gc; inspect orgs/entities/artifacts/freshness/health/interactions; alerts list/acknowledge/evaluate/mute/unmute/config; actions, outcomes, journey, org; all list commands support --json; sable-platform init bootstraps DB)
- Entity interaction edge table (directional handle-to-handle edges for relationship web visualization)
- Proactive alerting: tracking stale, cultist tag expiring, sentiment shift, MVL score change, unclaimed actions, workflow failures, discord pulse regression, discord pulse stale, stuck runs
- Alert delivery cooldown (4h default, configurable per org, dedup_key-scoped)
- Alert delivery failure tracking (last_delivery_error stamped on failed HTTP calls; queryable via list_alerts)
- Per-org failure isolation in evaluate_alerts() (one bad org does not abort remaining orgs)
- Workflow config versioning (step-name fingerprint on create; mismatch blocks resume)
- 237/237 tests passing

## Architecture Decisions

- **Synchronous workflow execution.** No threading. `poll_diagnostic` step requires manual `sable-platform workflow resume` after CultGrader finishes.
- **Subprocess adapters.** Clean boundary. No cross-repo imports in production paths.
- **DB path.** `SABLE_DB_PATH` env var or `~/.sable/sable.db`. Same file as before.
- **Migration path.** `importlib.resources` — no `SABLE_PROJECT_PATH` needed.
- **Jobs vs workflow tables.** Both coexist. `jobs/job_steps` = Slopper-internal. `workflow_runs/workflow_steps` = cross-suite coordination.

## Working Conventions

- Small patches over rewrites. Don't touch existing repo logic unless explicitly asked.
- Tests use in-memory SQLite — no `~/.sable/sable.db` modification.
- Adapters are subprocess-based; mock them in tests.
- All new workflows go in `sable_platform/workflows/builtins/` and self-register.
- Run the test suite with `python3 -m pytest tests/ -q`; all 237 tests must pass before merging.
- `StepDefinition` supports `skip_if` (predicate — skips step entirely if True), `max_retries` (default 3; set 0 for steps that must not retry), and `retry_delay_seconds` (default 5). Declare only when the step has a genuine transient failure mode or conditional path — defensive retry logic obscures determinism.
- To add a new alert type: (1) add `_check_my_condition(conn, org_id)` to `alert_checks.py`; (2) register it in `evaluate_alerts()` in `alert_evaluator.py`; (3) use `"{alert_type}:{entity_id}"` as the dedup_key.
- New alert tests must cover both the fire case and the cooldown suppression case.

## Key Files

| File | Purpose |
|------|---------|
| `sable_platform/db/connection.py` | DB entry point — get_db(), ensure_schema() |
| `sable_platform/db/workflow_store.py` | All workflow table CRUD |
| `sable_platform/db/alerts.py` | Alert CRUD — list_alerts(), mark_delivered(), mark_delivery_failed(), acknowledge_alert() |
| `sable_platform/db/interactions.py` | Interaction edge CRUD — sync_interaction_edges(), list_interactions(), get_interaction_summary() |
| `sable_platform/db/cost.py` | Cost tracking — log_cost(), check_budget(), get_weekly_spend() |
| `sable_platform/workflows/engine.py` | WorkflowRunner — the core state machine |
| `sable_platform/workflows/registry.py` | Register + look up named workflows |
| `sable_platform/workflows/alert_evaluator.py` | evaluate_alerts() — thin orchestrator |
| `sable_platform/workflows/alert_checks.py` | All 9 `_check_*` condition functions |
| `sable_platform/workflows/alert_delivery.py` | `_deliver()`, `_send_telegram()`, `_send_discord()` — HTTP delivery + cooldown gate |
| `sable_platform/cli/workflow_cmds.py` | CLI surface for operators |
| `docs/MIGRATION_PLAN.md` | Step-by-step migration for each existing repo |

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `SABLE_DB_PATH` | No | Path to `sable.db`. Defaults to `~/.sable/sable.db` |
| `SABLE_TELEGRAM_BOT_TOKEN` | No | Telegram bot token for alert delivery. If unset, Telegram delivery is silently skipped even when `telegram_chat_id` is configured on an org. |
| `SABLE_HOME` | No | Root dir for Sable config. Defaults to `~/.sable`. Used by `db/cost.py` to locate `config.yaml` for budget cap overrides. |

## Alert Delivery Cooldown

`_deliver()` in `alert_delivery.py` gates HTTP delivery (Telegram/Discord) by a per-`dedup_key`
cooldown window. After a successful delivery, `alerts.last_delivered_at` is stamped. On the next
`evaluate_alerts()` invocation, if the same `dedup_key` was delivered within `cooldown_hours`
(default: 4), the HTTP notification is suppressed. The alert DB record is always written; only
the external delivery is gated.

Rules:
- Cooldown scopes to `dedup_key`, not alert type or org.
- `last_delivered_at IS NULL` → treat as never delivered → fire.
- `cooldown_hours = 0` in `alert_configs` → cooldown disabled, always deliver.
- Cooldown does NOT reset on acknowledge/resolve — ages out naturally.
- Default: `cooldown_hours = 4` (set via `sable-platform alerts config set --org ORG cooldown-hours N`).

**Delivery failure tracking:** When HTTP delivery fails, `_deliver()` calls
`mark_delivery_failed(conn, dedup_key, error)` which stamps `alerts.last_delivery_error`
(truncated to 500 chars). On a subsequent successful delivery, `mark_delivered()` clears
`last_delivery_error = NULL`. Failures are queryable via `list_alerts()`.

**Dedup key format convention:** All checks in `alert_checks.py` must use
`"{alert_type}:{entity_or_run_id}"` as the dedup_key — e.g. `"stuck_run:{run_id}"`,
`"stale_tracking:{org_id}"`. Deviating silently breaks cooldown scoping: the key lookup will not
match prior records, and the same alert will re-fire every evaluation cycle.

## Workflow Config Versioning

`WorkflowRunner.run()` computes a fingerprint of the workflow's step names
(`sha1(sorted_names)[:8]`) and stores it in `workflow_runs.step_fingerprint`.

`WorkflowRunner.resume()` recomputes the fingerprint for the current definition. If the stored
fingerprint is non-NULL and mismatches, resume raises `SableError(STEP_EXECUTION_ERROR)` with a
message naming both fingerprints.

**Escape hatch:** `sable-platform workflow resume <run_id> --ignore-version-check` bypasses the
check. Use this only for emergency resumes when you have confirmed the structural change is safe
to apply to the in-flight run (e.g., a new step was added at the end, not renamed mid-run).

NULL stored fingerprint (runs created before migration 012) → validation is skipped silently.

## Entity Interaction Edges

`entity_interactions` table (migration 014) stores directional interaction edges between community
member handles. Designed as the data layer for SableWeb's relationship web visualization.

**Schema:** Each row is an aggregate edge: `(org_id, source_handle, target_handle, interaction_type)`
with a running `count`, `first_seen`, and `last_seen`. Types: `reply`, `mention`, `co_mention`.

**Sync:** `sync_interaction_edges(conn, org_id, edges, run_date)` upserts edges from Cult Grader's
`computed_metrics.json` when `reply_pairs` data is present. Idempotent: accumulates count, preserves
earliest `first_seen`, updates `last_seen` and `run_date`.

**CLI:** `sable-platform inspect interactions ORG [--type reply|mention|co_mention] [--min-count N] [--json]`

**Dependency:** Cult Grader Stage 4 must extract individual reply pairs before this table has data.

## Cost & Budget Tracking

`db/cost.py` tracks AI API spend per org against a weekly rolling cap.

- `log_cost(conn, org_id, call_type, cost_usd, ...)` — call after every external AI API invocation. Records to the `cost_events` table.
- `check_budget(conn, org_id)` — raises `SableError(BUDGET_EXCEEDED)` if 7-day rolling spend ≥ cap. Call this before LLM steps.
- Default cap: **$5.00/week per org.** Override via `orgs.config_json["max_ai_usd_per_org_per_week"]` or `platform.cost_caps.max_ai_usd_per_org_per_week` in `~/.sable/config.yaml`.
- At 90% of cap, a WARNING is logged but execution continues.
- `BUDGET_EXCEEDED` is a hard stop — the workflow step fails and does not resume without manual budget adjustment or cap increase.

Builtin workflows do not currently call `check_budget()` automatically. Cost responsibility lives
with the subprocess adapter that makes the LLM call. Any new workflow step that invokes an
external AI API must call `check_budget()` first.
