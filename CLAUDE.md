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
- `SableKOL` — KOL discovery, follow-graph extraction, and per-candidate
  Grok enrichment for cold outreach. Owns migrations 032-041 of `sable.db`
  (kol_candidates / kol_extract_runs / kol_follow_edges /
  kol_operator_relationships / kol_create_audit / kol_enrichment).
  Different integration pattern from the other 4 — invoked via its own
  FastAPI sidecar in the SableWeb compose stack, not via SP's workflow
  engine. See `SableKOL/CLAUDE.md`.

## Current State

**v0.5 is production-ready.** 2,450 tests pass locally (14 skipped, Postgres-conditional), 0 known cross-repo blockers. SQLAlchemy Core migration (Phases 0–13) complete: all runtime SQL is dialect-agnostic, insert-ID paths use backend-neutral `RETURNING`, Alembic assets packaged, Docker/compose with direct `alerts evaluate` loop, `pg_dump` backup. **Postgres is LIVE on the Hetzner VPS** (migrated 2026-04-09). Codex audit clean (2026-04-11). SS-COMPAT resolved in Slopper (2026-04-11). Live-Postgres CI suite exercised when `SABLE_TEST_POSTGRES_URL` is available.

**TIG trial build (in flight, target 5/1):** new `sable_platform/checkin/` module + `client_checkin_loop` workflow generates a weekly client-facing check-in (auto-sent to internal client TG chat for operator forwarding). Migration 031 added `metric_snapshots` for week-over-week deltas. Architecture: thin `Sable_Client_Comms` repo as a stub for the Adapter-pattern boundary; V1 LLM logic (Anthropic SDK direct, Opus 4.7 + prompt caching) lives in `sable_platform/checkin/` and migrates out post-trial. **First direct LLM dep on the platform** — contained to checkin module, acknowledged deviation from "no business logic" rule.

- **DB:** 62 migrations (032-041 SableKOL, 042 audit-review, 043-048 sable-roles discord layer + alert-triage, 049-053 Scored Mode V2, 054 state-pin, 055 media_assets, 056 operator reply-suggestions, 057 relay, 058 autocm, 059 work-tracking, 060 reply-clip-media, 061 reply-campaigns, 062 reply-opportunity-feed), WAL mode, busy_timeout=5s, all CRUD helpers, online backup, GC, health check
- **Contracts:** 8 cross-suite Pydantic models + JSON Schema export
- **Workflow engine:** synchronous, deterministic, retry/resume/skip_if, per-step timeout, config versioning, execution locking
- **9 builtin workflows:** prospect_diagnostic_sync, weekly_client_loop, alert_check, lead_discovery (with auto Cult Grader trigger for Tier 1), onboard_client, client_checkin_loop (TIG trial), autocm_kb_refresh, autocm_autonomy_sweep, autocm_weekly_digest
- **12 alert checks:** tracking stale, cultist tag expiring, sentiment shift, MVL score change, unclaimed actions, workflow failures, discord pulse regression, discord pulse stale, stuck runs, member decay, bridge decay, watchlist changes. Alert creation decoupled from delivery (`deliver_alerts_by_ids()` called after commit).
- **Delivery:** Telegram, Discord, HMAC-SHA256 webhooks. Cooldown (4h default), per-org config, mute/unmute.
- **CLI:** full operator surface — workflows, alerts, inspect (13 subcommands including prospect_pipeline), actions, outcomes, journey, dashboard, watchlist, webhooks, cron, org config (sector/stage enums, numeric range validation), backup, init, gc, health-server, metrics, migrate
- **Adapters:** subprocess-based for CultGrader, SableTracking, Slopper, LeadIdentifier, ClientComms (V1 stub). SableKOL is NOT a subprocess adapter — it's a sidecar (FastAPI service in SableWeb's compose network) and the SableWeb ops surface is its primary caller.
- **Infra:** Dockerfile, CI (ruff + pytest), structured JSON logging, cron scheduler

## Architecture Decisions

- **Synchronous workflow execution.** Threading is used only for per-step timeouts (`StepDefinition.timeout_seconds`). `poll_diagnostic` step requires manual `sable-platform workflow resume` after CultGrader finishes.
- **Subprocess adapters.** Clean boundary. No cross-repo imports in production paths.
- **Vendored `sable_pulse_core` (`sable_platform/_vendor/sable_pulse_core/`).** SableAutoCM (`sable_platform.autocm`) reuses sable-pulse's deterministic CM engine (hard-refusal safety bank, engagement filter, slot-fill KB, NULO persona/template renderer). The literal pillar-1 rule ("No cross-repo imports in production paths") forbids a production `import sable_pulse.*` of the sibling repo, so the engine is **vendored in-tree** — a one-way synced copy under `_vendor/sable_pulse_core/`, and AutoCM imports `sable_platform._vendor.sable_pulse_core`, never `sable_pulse.*`. This satisfies pillar 1 (nothing imports a sibling repo) but is a **SECOND acknowledged deviation from the "no specialized-repo business logic" rule** (pillar 2) — peer to the `checkin/` deviation above, but sharper: `checkin/` is native SP code, this is copied from a sibling. **Owner: Sieggy (AutoCM / sable-pulse track).** **Constraint: `_vendor/sable_pulse_core` is GENERATED, NEVER EDITED IN PLACE** — it is refreshed only by re-running the donor's sync script (`sable-pulse/scripts/sync_vendor.py --dest …/sable_platform/_vendor/sable_pulse_core`), never hand-edited (a prod hot-fix to the vendored safety bank must land in the donor and re-sync, not in place). The copy carries a `VENDOR_SNAPSHOT.json` with a SHA-256 content hash over the full code+data artifact set; **drift is CI-gated** by `tests/autocm/test_vendor_drift.py`, which fails loudly if the vendored tree was edited in place OR the donor advanced without a re-sync, and asserts the vendored safety bank is a superset of `SableAutoCM/docs/SAFETY.md` §1 (6 hard-refusal categories) + §3 (6 content blocks). See MEGAPLAN D-1 / R-1 / R-1a.
- **DB target.** `SABLE_DATABASE_URL` when set; otherwise `SABLE_DB_PATH` or `~/.sable/sable.db`.
- **Migration path.** `importlib.resources` — no `SABLE_PROJECT_PATH` needed.
- **Jobs vs workflow tables.** Both coexist. `jobs/job_steps` = Slopper-internal. `workflow_runs/workflow_steps` = cross-suite coordination.

## Working Conventions

- Small patches over rewrites. Don't touch existing repo logic unless explicitly asked.
- Tests use in-memory SQLite — no `~/.sable/sable.db` modification.
- Adapters are subprocess-based; mock them in tests.
- All new workflows go in `sable_platform/workflows/builtins/` and self-register.
- Run the test suite with `python3 -m pytest tests/ -q`; all tests must pass before merging.
- `StepDefinition` supports `skip_if` (predicate — skips step entirely if True), `max_retries` (default 1; set 0 for steps that must not retry), `retry_delay_seconds` (default 0), and `timeout_seconds` (default None — no timeout). Steps that exceed `timeout_seconds` return `StepResult(status="failed", error="step_timeout")`.
- To add a new alert type: (1) add `_check_my_condition(conn, org_id)` to `alert_checks.py` — check functions must NOT call `_deliver()`, they only call `create_alert()` and return alert IDs; (2) register it in `evaluate_alerts()` in `alert_evaluator.py`; (3) the caller (CLI or builtin workflow step) calls `deliver_alerts_by_ids(conn, alert_ids)` after evaluation; (4) use `"{alert_type}:{org_id}:{entity_or_run_id}"` as the dedup_key. Always include `org_id` to prevent cross-org collision. Org-scoped alerts with no per-entity key use `"{alert_type}:{org_id}"` (2 parts). Never omit org_id.
- New alert tests must cover both the fire case and the cooldown suppression case.
- **Dual-migration requirement:** Schema changes require both a SQL migration file (registered in `_MIGRATIONS` in `connection.py`) for SQLite AND an Alembic revision (`alembic revision --autogenerate`) for Postgres. Missing either causes SQLite/Postgres schema drift.

## Key Files

| File | Purpose |
|------|---------|
| `sable_platform/db/connection.py` | DB entry point — get_db(), ensure_schema(), sable_db_path() |
| `sable_platform/db/backup.py` | SQLite online backup — backup_database(), _prune_old_backups() |
| `sable_platform/cron.py` | Crontab scheduler — add_entry(), remove_entry(), list_entries() |
| `sable_platform/db/workflow_store.py` | All workflow table CRUD |
| `sable_platform/db/alerts.py` | Alert CRUD — list_alerts(), mark_delivered(), mark_delivery_failed(), acknowledge_alert() |
| `sable_platform/db/interactions.py` | Interaction edge CRUD — sync_interaction_edges(), list_interactions(), get_interaction_summary() |
| `sable_platform/db/decay.py` | Decay score CRUD — sync_decay_scores(), list_decay_scores(), get_decay_summary() |
| `sable_platform/db/prospects.py` | Prospect score CRUD — sync_prospect_scores(), list_prospect_scores(), get_prospect_summary() |
| `sable_platform/db/cost.py` | Cost tracking — log_cost(), check_budget(), get_weekly_spend() |
| `sable_platform/db/prospect_pipeline.py` | Prospect pipeline query — JOINs prospect_scores with diagnostic_runs |
| `sable_platform/workflows/engine.py` | WorkflowRunner — the core state machine |
| `sable_platform/workflows/registry.py` | Register + look up named workflows |
| `sable_platform/workflows/alert_evaluator.py` | evaluate_alerts() — thin orchestrator |
| `sable_platform/workflows/alert_checks.py` | All 12 `_check_*` condition functions |
| `sable_platform/workflows/alert_delivery.py` | `deliver_alerts_by_ids()`, `_deliver()`, `_send_telegram()`, `_send_discord()` — HTTP delivery + cooldown gate |
| `sable_platform/db/centrality.py` | Centrality score CRUD — sync_centrality_scores(), list_centrality_scores(), get_centrality_summary() |
| `sable_platform/db/watchlist.py` | Watchlist CRUD + snapshot-based change detection |
| `sable_platform/db/audit.py` | Audit log — log_audit(), list_audit_log() |
| `sable_platform/db/webhooks.py` | Webhook subscription CRUD — SSRF validation, secret masking, auto-disable |
| `sable_platform/webhooks/dispatch.py` | Webhook dispatch — HMAC-SHA256 signing, fire-and-forget delivery |
| `sable_platform/cli/workflow_cmds.py` | CLI surface for operators (includes preflight gate) |
| `sable_platform/cli/dashboard_cmds.py` | Operator dashboard — urgency-sorted attention view |
| `sable_platform/cli/watchlist_cmds.py` | Watchlist CLI — add/remove/list/changes/snapshot |
| `sable_platform/cli/webhook_cmds.py` | Webhook CLI — add/list/remove/test |
| `sable_platform/db/playbook.py` | Playbook tagging CRUD — upsert_playbook_targets(), record_playbook_outcomes() |
| `sable_platform/db/snapshots.py` | Metric snapshot CRUD (mig 031) — upsert_metric_snapshot(), get_latest_snapshot(), list_snapshots(). Used by client_checkin_loop for WoW deltas. JSON metrics blob is opaque to platform — callers own shape. |
| `sable_platform/db/gc.py` | Data retention GC — run_gc(), FK-safe deletion, audit log immune |
| `sable_platform/db/health.py` | Programmatic health check — check_db_health() |
| `sable_platform/logging_config.py` | Structured JSON logging — StructuredFormatter, configure_logging() |
| `sable_platform/contracts/export.py` | JSON Schema export — export_schemas() for 8 Pydantic models |
| `sable_platform/contracts/tracking.py` | TrackingMetadata contract — 17 versioned fields for SableTracking |
| `sable_platform/contracts/leads.py` | Lead + DimensionScores contracts, PURSUE/MONITOR threshold constants |
| `sable_platform/cli/main.py` | Top-level CLI: init, backup, schema, gc |
| `sable_platform/cli/org_cmds.py` | Org CLI: create, list, graduate, reject |
| `sable_platform/db/entities.py` | Entity CRUD + add_entity_note(), list_entity_notes() |
| `sable_platform/media/` | Shared media layer (mig 055 `media_assets`): `R2Store` (sync boto3 upload/presign), `sanitize` (key/filename safety), `urls.build_media_url` (`<bucket>/<key>`→proxy URL), `registry.register_asset` (idempotent on `(org_id,r2_ref)`). Canonical for Slopper clips + Tracking media. See `docs/SHARED_MEDIA_LAYER_PLAN_V1.md`. |
| `sable_platform/db/work_tracking.py` | Operator work-tracking (mig 059, SW-TASKING Phase 1): `open_mod_slot`/`close_mod_slot` (operator-scoped close-before-open), `list_active_slots`, `list_sessions`, `log_work_event`, `get_work_summary` (the ops "scale of work delivered" rollup — replies *measured*, coverage hours/communities *self-reported*, open slots excluded from hours). Replies are counted from `reply_outcomes` via `replies.count_replies_delivered` (single source of truth — not mirrored). SableWeb (`/ops`) is the writer/reader; SP does not invoke it. See `SableWeb/docs/SW_TASKING_PHASE1_PLAN.md`. |
| `sable_platform/relay/db.py` | Relay table CRUD (mig 057/058 substrate). Reply-opportunity-feed helpers (mig 062, see `SableRelay/REPLY_OPPORTUNITY_FEED_PLAN.md`): `get_or_create_sweep_sentinel`, `find_active_opportunity_for_tweet`, `upsert_sweep_opportunity` (app-level dedup — no `UNIQUE(org_id,tweet_id)`), `list_feed_opportunities` (per-operator, org-filtered, dismiss/snooze-aware), `set_operator_opportunity_state`, `mark_opportunity_handled`, `record_opportunity_feedback` (the two thumbs); sweep state machine `get_sweep_config`/`upsert_sweep_config`/`mark_sweep_requested`/`mark_sweep_completed`/`list_due_sweep_orgs`; `relay_tweets` read-through cache `upsert_relay_tweet`/`get_cached_relay_tweet`; heartbeat gate `write_operator_heartbeat`/`has_recent_heartbeat`; `gc_expired_opportunities`. Writers require an `immediate_txn`; reads are transaction-free. |
| `docs/CLI_REFERENCE.md` | Complete CLI command reference |
| `docs/CROSS_REPO_INTEGRATION.md` | Adapter reference, data flows, direct commands |
| `docs/SCHEMA_CONTRACTS.md` | Cross-suite data contracts — entity status, tiers, dimensions, cost models, artifacts, outcomes |
| `docs/ALERT_SYSTEM.md` | Alert lifecycle, all 12 checks, dedup key formats, delivery channels, threshold overrides |
| `docs/CLIENT_LIFECYCLE.md` | Client lifecycle stages → CLI commands → SableWeb views |
| `docs/EXTENDING.md` | How to add a workflow, adapter, alert check, or migration |
| `docs/schemas/` | Generated JSON Schema files for all 8 Pydantic contracts |

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `SABLE_DB_PATH` | No | Path to `sable.db`. Defaults to `~/.sable/sable.db` |
| `SABLE_DATABASE_URL` | No | SQLAlchemy database URL. When set, takes precedence over `SABLE_DB_PATH` for the connection factory. Supports `sqlite:///path` and `postgresql://...`. Backup CLI auto-dispatches to `pg_dump` when this starts with `postgresql`. |
| `SABLE_TELEGRAM_BOT_TOKEN` | No | Telegram bot token for alert delivery. If unset, Telegram delivery is silently skipped even when `telegram_chat_id` is configured on an org. |
| `SABLE_HOME` | No | Root dir for Sable config. Defaults to `~/.sable`. Used by `db/cost.py` to locate `config.yaml` for budget cap overrides. |
| `SABLE_OPERATOR_ID` | **Yes** | Operator identity stamped on `workflow_runs.operator_id` and `audit_log.actor`. CLI fails closed (exit 1) if unset or `"unknown"` for all commands except `init`. |
| `SABLE_HEALTH_TOKEN` | **Yes (health-server)** | Bearer token for the `/health` HTTP endpoint. `health-server` refuses to start if unset. Generate with `openssl rand -hex 32`. |
| `SABLE_CULT_GRADER_PATH` | No | Path to Cult Grader repo. Required by `CultGraderAdapter`. |
| `SABLE_TRACKING_PATH` | No | Path to SableTracking repo. Required by `SableTrackingAdapter`. |
| `SABLE_SLOPPER_PATH` | No | Path to Slopper repo. Required by `SlopperAdvisoryAdapter`. |
| `SABLE_LEAD_IDENTIFIER_PATH` | No | Path to Lead Identifier repo. Required by `LeadIdentifierAdapter`. |
| `SABLE_CLIENT_COMMS_PATH` | No | Path to Sable_Client_Comms repo. Required by `SableClientCommsAdapter` (V1 stub — no-op exit 0; real check-in synthesis lives in `sable_platform.checkin` until post-trial migration). |

## Alert Dedup & Delivery

- **Dedup policy:** `create_alert()` blocks when existing alert with same `dedup_key` has `status IN ('new', 'acknowledged')`. Only `resolved` allows re-alerting.
- **Dedup key format:** `"{alert_type}:{org_id}:{entity_or_run_id}"` (3 parts) for entity/run-scoped alerts; `"{alert_type}:{org_id}"` (2 parts) for org-scoped alerts. Always include org_id — omitting it causes cross-org alert suppression collisions.
- **Cooldown:** 4h default per `dedup_key`. `cooldown_hours=0` disables. Does NOT reset on ack/resolve.
- **Delivery failure:** `last_delivery_error` stamped on HTTP failure, cleared on next success.

See `docs/THREAT_MODEL.md` § Alert Dedup and `docs/SCHEMA_CONTRACTS.md` § Alert Severity & Status for full enum values.

## Workflow Config Versioning

Step-name SHA1 fingerprint stored on `run()`, checked on `resume()`. Mismatch blocks resume. `--ignore-version-check` bypasses. NULL fingerprint (pre-migration-012) skips check.

## Prospect Scores Schema Note

`prospect_scores.org_id` stores the **prospect's project_id** (the external crypto community being evaluated by Lead Identifier), NOT the Sable client org_id. This column was named `org_id` to match SQLite FK conventions but is semantically a prospect identifier. `graduate_prospect(conn, project_id)` and `reject_prospect(conn, project_id)` use it as a project_id; `list_prospect_scores()` has no Sable client filter (returns all prospects globally — single-operator assumption). If multi-tenant support is added, a migration will be needed to add a `client_org_id` column.

## Org config_json Convention

`orgs.config_json` is a JSON blob used for three purposes: (1) cost cap overrides, (2) alert threshold overrides, (3) org metadata (sector/stage). Set via `sable-platform org config set ORG KEY VALUE`.

**Sector** (validated enum): `DeFi`, `DeSci`, `Gaming`, `Infrastructure`, `L1/L2`, `Social`, `DAO`, `NFT`, `AI`, `Other`

**Stage** (validated enum): `pre_launch`, `launch`, `growth`, `mature`, `declining`

SableWeb reads `sector` and `stage` from this field. Alert checks read threshold override keys at evaluation time (no restart needed). See `docs/ALERT_SYSTEM.md` § Per-Org Threshold Overrides for the full threshold key list.

**Check-in keys** (read by `client_checkin_loop` notify step): `checkin_enabled` (truthy: True / "true"/"yes"/"1"/"on") and `client_telegram_chat_id` (string, e.g. internal Sable↔TIG chat). Set via `sable-platform org config set ORG checkin_enabled true` and `sable-platform org config set ORG client_telegram_chat_id -- -5050566880` (the `--` is required when the chat ID starts with a minus sign so click doesn't read it as a flag).

## Key Journeys

`get_key_journeys(conn, org_id, limit=5)` in `db/journey.py` returns the N most event-rich entity journeys for an org. Scores by total event count (tag history + actions + outcomes), calls `get_entity_journey()` for each. Exposed as `sable-platform journey top --org ORG [--limit N] [--json]`. This is the primary feed for SableWeb's `key_journeys` field.

## Cost & Budget Tracking

`check_budget()` raises `BUDGET_EXCEEDED` if 7-day rolling spend >= cap ($5/week default, configurable per org). Builtin workflows don't call it automatically — cost responsibility is on the subprocess adapter making the LLM call.
