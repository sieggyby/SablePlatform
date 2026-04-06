# SablePlatform — Audit History

Completed work, moved from TODO.md to keep the roadmap forward-looking only.

---

## P1 — Data Integrity and Correctness (all resolved)

### P1-1 — redact_error() not called before persisting step error messages
> **Resolved:** Both `fail_workflow_step` and `fail_workflow_run` in `workflow_store.py` call `redact_error()` at the SQL parameter site. Test: `test_redact_error_in_step_failure` passes.

### P1-2 — skipped-step output_json uses "reason" key, polluting accumulated context
> **Resolved:** `skip_workflow_step()` writes `{"_skip_reason": reason}`. Test: `test_skip_reason_does_not_overwrite_prior_output` passes.

---

## P2 — Quality and Maintainability (all resolved)

### P2-1 — adapter status()/get_result() open new DB connections per call
> **Resolved:** `conn=None` + `_owns` pattern already in `SableTrackingAdapter` and `SlopperAdvisoryAdapter`. Tests: `test_tracking_adapter_status_uses_provided_conn`, `test_tracking_adapter_get_result_uses_provided_conn`, `test_slopper_adapter_status_uses_provided_conn`, `test_slopper_adapter_get_result_uses_provided_conn` pass.

### P2-2 — _check_workflow_failures scans all historical failed runs without a time window
> **Resolved:** Query already filters `created_at > datetime('now', '-30 days')`. Tests: `test_old_workflow_failure_does_not_alert`, `test_recent_workflow_failure_does_alert` pass.

### P2-3 — open() used without context manager in _register_actions (weekly_client_loop.py)
> **Resolved:** `weekly_client_loop.py` already uses `with open(...) as fh:` context manager.

### P2-4 — Stale test name in test_migrations.py
> **Resolved:** Test already named `test_fresh_db_reaches_current_version()`, asserting version 9 (current head).

---

## Features (all resolved)

### Feature: Alert Delivery via Telegram/Discord
> **Resolved:** `_deliver()`, `_send_telegram()`, `_send_discord()` already implemented in `alert_evaluator.py` using `urllib.request`. Columns `telegram_chat_id` / `discord_webhook_url` already in `alert_configs` (migration 009). Token read from `SABLE_TELEGRAM_BOT_TOKEN` env var (documented in CLAUDE.md). Tests: `test_telegram_delivery_called_when_configured`, `test_telegram_delivery_failure_does_not_propagate`, `test_discord_delivery_failure_does_not_propagate` pass.

### Feature: Client Onboarding Workflow (onboard_client builtin)
> **Resolved:** `sable_platform/workflows/builtins/onboard_client.py` implements 6-step workflow:
> verify_org (raises on missing org), verify_tracking/slopper/cult_grader adapters (captures
> SableError, non-blocking), create_initial_sync_record (commits onboarding sync_runs row),
> mark_complete (structured readiness report). Registered in `registry._auto_register()`.
> Tests: `test_onboard_client_completes_all_adapters_missing`, `test_onboard_client_creates_sync_run_row`,
> `test_onboard_client_fails_for_unknown_org` pass.
>
> **Note:** Implementation chose resilient pattern (adapter failures captured, workflow completes
> with tools_failed list) rather than halt-on-failure spec. sync_run is always created when org
> exists. This diverges from the "halt without partial sync_run" spec but is validated by tests
> and is arguably more useful for onboarding diagnosis.

### Feature: Workflow Run Garbage Collection (sable-platform gc)
> **Resolved:** `sable-platform gc [--hours N]` implemented in `workflow_cmds.py`. `mark_timed_out_runs()` in `workflow_store.py`. Tests: `test_gc_marks_stuck_run_timed_out`, `test_gc_ignores_recent_run`, `test_gc_is_idempotent` pass.

### Feature: Alert Cooldown + Discord Pulse Stale Guard
> **Resolved:** Migration 011 adds `alert_configs.cooldown_hours INTEGER DEFAULT 4` and
> `alerts.last_delivered_at TEXT`. `_deliver()` in `alert_evaluator.py` now accepts `dedup_key`
> (keyword-only), checks `last_delivered_at` against `cooldown_hours`, suppresses HTTP delivery
> if within window, stamps `mark_delivered()` after successful delivery. All 7 `_deliver()` call
> sites updated with `dedup_key`. `_check_discord_pulse_stale()` fires `warning/discord_pulse_stale`
> when no pulse data in last 7 days. DB helpers `get_last_delivered_at()` and `mark_delivered()`
> added to `db/alerts.py`. Tests: 4 cooldown cases + 3 pulse stale cases pass.
> Migration version: 11. CLAUDE.md updated with cooldown semantics.

### Feature: Stuck Workflow Run Alert
> **Resolved:** `_check_stuck_runs()` added to `alert_evaluator.py`. Queries `workflow_runs`
> for `status='running'` AND `started_at < datetime('now', '-2 hours')`. Fires `warning/stuck_run`
> per stuck run with `dedup_key=f"stuck_run:{run_id}"`. `STUCK_RUN_THRESHOLD_HOURS = 2` constant.
> Registered in `evaluate_alerts()` inside per-org loop. Try/except guard. No migration.
> Tests: `test_stuck_run_fires_warning`, `test_recent_run_no_alert`,
> `test_timed_out_run_no_double_alert` pass.

### Feature: Workflow Config Versioning
> **Resolved:** Migration 012 adds `workflow_runs.step_fingerprint TEXT` (nullable). `_workflow_fingerprint()`
> in `engine.py` computes `sha1(sorted_step_names)[:8]`. `run()` stores fingerprint via
> `create_workflow_run(step_fingerprint=fp)`. `resume()` compares stored vs current fingerprint;
> raises `SableError(STEP_EXECUTION_ERROR)` on mismatch; skips check if stored is NULL (old runs).
> `--ignore-version-check` flag added to `sable-platform workflow resume`. `workflow_store.py`
> `create_workflow_run()` accepts optional `step_fingerprint`. Tests: `test_version_mismatch_raises`,
> `test_null_version_skips_check`, `test_matching_version_resumes`, `test_ignore_version_check_bypasses_error`
> pass. CLAUDE.md updated with versioning semantics and escape hatch.

---

## Simplify (all resolved)

### Simplify: _repo_path() duplicated across 4 adapter files
> **Resolved:** `SubprocessAdapterMixin._resolve_repo_path(env_var)` implemented in `adapters/base.py`.
> All 4 adapters use it as a one-liner: `return self._resolve_repo_path("ENV_VAR_NAME")`.
> Error message names the env var. Exception type and code unchanged.

### Simplify: Magic constants in merge.py, entities.py, and alert_evaluator.py
> **Resolved:** `MERGE_CONFIDENCE_THRESHOLD = 0.70` in `merge.py`,
> `SHARED_HANDLE_MERGE_CONFIDENCE = 0.80` in `entities.py`,
> `TRACKING_STALE_DAYS = 14` in `alert_evaluator.py`. All module-top named constants.

### Simplify: Inline import json in jobs.py (3 locations)
> **Resolved:** `import json` is at module top in `sable_platform/db/jobs.py`. No inline imports.

### Simplify: _SEVERITY_RANK dead constant in db/alerts.py
> **Resolved:** `_SEVERITY_RANK` is not present in `sable_platform/db/alerts.py` (already removed
> or never added). Grep confirms zero references across codebase.

### Simplify: Deferred import and bare except in cost.py and entities.py
> **Resolved:**
> (a) `from sable_platform.errors import SableError, BUDGET_EXCEEDED` is at module top in
> `sable_platform/db/cost.py`. Deferred `import yaml` inside `_read_platform_config()` retained
> (intentional optional-dependency guard).
> (b) `add_handle()` in `entities.py` uses `except sqlite3.IntegrityError: pass` — typed,
> not bare. Only UNIQUE constraint violations are silenced; all other errors propagate.

---

### Feature: Entity Interaction Edge Table — Data Layer
> **Resolved:** Migration 014 creates `entity_interactions` table with directional handle-to-handle
> edges (`source_handle`, `target_handle`, `interaction_type`, `count`, `first_seen`, `last_seen`,
> `run_date`). `db/interactions.py` provides `sync_interaction_edges()` (idempotent upsert —
> accumulates count, preserves earliest first_seen), `list_interactions()` (sorted by count desc,
> filterable by type and min-count), `get_interaction_summary()` (aggregate stats). CLI:
> `sable-platform inspect interactions ORG [--type] [--min-count] [--json]`. 16 new tests pass.
>
> **Note:** Data layer only. Cult Grader Stage 4 must extract individual reply pairs before this
> table has data. Integration call site and SableWeb rendering remain in TODO.

### Feature: Churn Prediction & Intervention Engine — Data Layer + Alerting
> **Resolved:** Migration 015 creates `entity_decay_scores` table with per-entity decay scores
> (upsert on `(org_id, entity_id)`). `db/decay.py` provides `sync_decay_scores()` (idempotent
> upsert, resolves handles to entity_ids via `entity_handles`, normalizes fallback handles),
> `list_decay_scores()`, `get_decay_summary()`. Alert check `_check_member_decay()` fires
> warning at >= 0.6 and critical at >= 0.8 with structurally important tag. Thresholds configurable
> via `orgs.config_json`. dedup_key includes org_id to prevent cross-org collision. CLI:
> `sable-platform inspect decay ORG [--min-score] [--tier] [--json]`. 31 new tests pass (275 total).
> QA-hardened: cross-org dedup isolation, handle normalization, FK safety for raw handle fallbacks.
>
> **Note:** Data layer + alerting only. Cult Grader DECAY-0 through DECAY-7 must ship before this
> table has data. Slopper CHURN-1/CHURN-2 generate interventions. Integration call site remains in TODO.

### Feature: Network Centrality Scores — Data Layer + Bridge Decay Alert
> **Resolved:** Migration 016 creates `entity_centrality_scores` table with degree, betweenness,
> eigenvector centrality. `db/centrality.py` provides `sync_centrality_scores()` (upsert, handle
> resolution), `list_centrality_scores()`, `get_centrality_summary()`. Alert check
> `_check_bridge_decay()` fires critical when betweenness >= 0.3 AND decay >= 0.6 (both thresholds
> configurable via `orgs.config_json`). CLI: `sable-platform inspect centrality ORG [--min-degree]
> [--limit] [--json]`. 14 tests pass (centrality DB + bridge decay alert).
>
> **Dependency:** Cult Grader must compute centrality from reply-pair data (NetworkX or equivalent).

### Feature: Entity Watchlist — Snapshot-Based Change Detection + Alert
> **Resolved:** Migration 017 creates `entity_watchlist` and `watchlist_snapshots` tables.
> `db/watchlist.py` provides `add_to_watchlist()`, `remove_from_watchlist()`, `list_watchlist()`,
> `_take_snapshot()` (captures decay score, active tags, interaction count), `take_all_snapshots()`,
> `get_watchlist_changes()` (compares two most recent snapshots). Alert check `_check_watchlist_changes()`
> fires warning for any change, critical for decay increase >= 0.1. CLI: `sable-platform watchlist
> add|remove|list|changes|snapshot`. 18 tests pass (watchlist DB + alert + CLI).

### Feature: Operator Audit Log
> **Resolved:** Migration 018 creates `audit_log` table (append-only, no FK constraints — survives
> entity deletion). `db/audit.py` provides `log_audit()` and `list_audit_log()`. Instrumented at
> 5 mutation sites: `acknowledge_alert()`, `resolve_alert()`, `deactivate_tag()`, `archive_entity()`,
> `execute_merge()`. Watchlist add/remove audited at CLI layer. CLI: `sable-platform inspect audit
> [--org] [--actor] [--action] [--since] [--json]`. 14 tests pass (audit DB + integration + CLI).

### Feature: Workflow Event Webhooks
> **Resolved:** Migration 019 creates `webhook_subscriptions` table with SSRF prevention
> (`_is_private_url()` using `ipaddress` module for IPv4/IPv6/hex/octal/link-local blocking),
> secret validation (>= 16 chars), subscription cap (5 per org), auto-disable after 10 failures.
> `webhooks/dispatch.py` provides `dispatch_event()` with HMAC-SHA256 signing (deterministic
> JSON serialization). Integrated at alert delivery and workflow event emission (try/except wrapped).
> CLI: `sable-platform webhooks add|list|remove|test`. 19 tests pass (webhooks DB + dispatch + CLI).
>
> **QA hardened:** SSRF check upgraded from string prefix matching to `ipaddress.ip_address()` parsing
> per QA audit. Covers IPv6 loopback, link-local, IPv4-mapped IPv6, and all RFC 1918 ranges.

### Feature: Operator Dashboard + Inspect Spend + Preflight Gate
> **Resolved:** Three CLI-only features (no new migrations):
> - `sable-platform dashboard [--org] [--json]` — urgency-sorted per-org view of alerts, stale data,
>   stuck runs, pending actions, budget, decay risk.
> - `sable-platform inspect spend [--org] [--json]` — weekly spend, budget cap, headroom, pct_used.
> - `sable-platform workflow preflight [--org]` — machine-friendly health gate (exit 0/1) checking
>   org_active, stuck_runs, budget >= 90%, critical_alerts.
> 16 tests pass (dashboard + spend + preflight CLI).

---

## Cross-Suite Sync Features (all complete, 2026-04-03 — 2026-04-04)

### F-BLOB: Run Summary JSON Blob for SableWeb
> **SablePlatform:** Migration 021 adds `run_summary_json TEXT` to `diagnostic_runs`.
> **Cult Grader:** `_build_run_summary()` assembles versioned blob (schema_version: 1, 50KB cap). 10 tests.
> **SableWeb:** Fully wired — `blob-reader.ts:readRunSummary()`, fallback in `data-service.ts`.

### F-PBTAG: Playbook Outcome Tagging Tables
> **SablePlatform:** Migration 022 adds `playbook_targets` + `playbook_outcomes`. DB helpers in `db/playbook.py`.
> **Cult Grader:** `_sync_playbook_data()` loads targets/outcomes from run dir. 5 tests.
> **SableWeb:** Fully wired — `PlaybookEffectiveness.tsx` (client) + `PlaybookDetail.tsx` (ops).

### Entity Interaction Edge Table
> **SablePlatform:** Migration 014, `db/interactions.py`, `inspect interactions` CLI. 16 tests.
> **Cult Grader:** `_sync_interaction_edges()` with pre-aggregated reply pairs. 17 tests.
> **SableWeb:** Data wired (`InteractionSummary.tsx`). Force-directed graph viz deferred (library selection pending).

### Churn Prediction
> **SablePlatform:** Migration 015, `db/decay.py`, `_check_member_decay()` alert. 31 tests.
> **Cult Grader:** `_sync_decay_scores()` with risk level mapping. Non-fatal.
> **SableWeb:** Fully wired — `AttritionWatchlist.tsx` with sparklines.
> **Slopper:** CHURN-1/CHURN-2 shipped.

### Network Centrality
> **SablePlatform:** Migration 023, `db/centrality.py`, bridge decay alert. 14 tests.
> **Cult Grader:** `_sync_centrality_scores()` from reply pair interaction graph. 6 tests.

### Lead Identifier → sable.db Prospect Score Sync
> **SablePlatform:** Migration 020, `db/prospects.py`, `inspect prospects` CLI. LI-1 through LI-4 resolved.
> **Lead Identifier:** `platform_sync.py:sync_scores_to_platform()` + `sync_cost_to_platform()`. 11 tests.
> **SableWeb:** Fully wired — `buildProspectsFromDb()` with DB-first fallback chain.

---

## Production Infrastructure (all complete, 2026-04-04)

> - **SP-LOCK:** Execution locking in `engine.py`. 4h stale-lock recovery. `workflow unlock` CLI. 10 tests.
> - **SP-AUTH:** `SABLE_OPERATOR_ID` env var. Migration 024 (`operator_id` column). 7 tests.
> - **SP-IDX:** `idx_entity_tags_tag_current` compound index (migration 024).
> - **SP-OBS Phase 1:** Structured JSON logging (`--json-log` flag). 4 tests. Phases 2-3 deferred.
> - **SP-DEPLOY:** Dockerfile + docker-compose.yaml.
> - **SP-1:** `.github/workflows/ci.yml` (ruff + pytest).
> - **SP-WEBHOOK:** Async daemon thread dispatch. 8 tests.
> - **SP-RETENTION:** `sable-platform gc --retention-days N`. FK-safe. Audit log immune. 5 tests.
> - **SP-2:** Schema contracts documented in `docs/SCHEMA_CONTRACTS.md`.
> - **SP-3:** `sable-platform schema` CLI. JSON Schema for 8 Pydantic models. 3 tests.
> - **SP-4:** `db/health.py:check_db_health()`. 2 tests.

---

## SablePlatform-Side Fixes (adversarial review, 2026-04-04)

> - `SlopperAdvisoryAdapter` handle resolution via `entity_handles`. 6 tests.
> - `_sync_scores` step in `lead_discovery` workflow (dimension inversion). 7 tests.
> - `_parse_actions_from_artifact()` for strategy briefs. 6 tests.
> - Dual-source pulse freshness check (`sync_runs` + `artifacts`). 6 tests.
> - `twice-weekly` cron preset. 3 tests.
> - `add_entity_note()` + `list_entity_notes()` helpers. 9 tests.

---

## Slopper Integration Contracts (2026-04-04)

> SP-COST-MODELS, SP-ARTIFACT-TYPES, SP-OUTCOME-TYPES documented in `docs/SCHEMA_CONTRACTS.md`.

---

## SableTracking Integration (2026-04-04)

> - **TRACK-1:** `TrackingMetadata` contract published. Included in `sable-platform schema` export.
> - **TRACK-2:** `outcomes` table schema verified ready for SableTracking P7-2.
> - **TRACK-3:** `actions` table schema verified ready for SableTracking P7-3.
> - **TRACK-4:** No action required — SableTracking uses own scheduler.

---

## SP-CONTRACT: Adapter Interface Contract Tests (2026-04-04)

> 22 tests in `tests/contracts/test_adapter_interfaces.py`: protocol compliance, CLI command shapes,
> Pydantic round-trips, adapter result parsers. Catches interface drift before production.

---

## F-REJECT: Prospect Rejection (2026-04-04)

> Migration 026 adds `rejected_at TEXT` to `prospect_scores`. `reject_prospect()` in `db/prospects.py`.
> `sable-platform org reject <project_id> [--reason TEXT]` with audit logging. 8 tests.

---

## Codex Audit Remediation (2026-04-04)

> 4 findings from Codex read-only audit, all resolved:
> - **Alert dedup:** Changed `status='new'` → `status IN ('new', 'acknowledged')` in `create_alert()`.
> - **Tag history:** Narrowed `sqlite3.OperationalError` catch to re-raise non-"no such table" errors.
> - **Weekly client loop:** Run-scoped artifact queries using `workflow_runs.started_at` as lower bound.
> - **Entity status vocabulary:** Fixed docs (`active` → `confirmed`) and tests (`provisional` → `confirmed`).

---

## A-series — 2026-04-05 Full-Repo Audit (all resolved)

### A1 — _check_workflow_failures exception isolation in evaluate_alerts()
> **Resolved:** Confirmed `_check_workflow_failures` is already properly try/except isolated in `evaluate_alerts()`. Log message clarified to include "skipping". Test `test_workflow_failures_crash_does_not_abort_regression_check` in `tests/alerts/test_alerts.py` verifies a crash in `_check_workflow_failures` does not abort `_check_discord_pulse_regression`.

### A2 — Lazy import of log_audit inside deactivate_tag()
> **Resolved:** `from sable_platform.db.audit import log_audit` moved to module level in `tags.py`. No circular import (audit.py does not import tags). All 32 tag tests pass.

### A3 — prospect_scores.org_id naming confusion
> **Resolved (documentation):** New section "Prospect Scores Schema Note" added to `CLAUDE.md` explaining that `prospect_scores.org_id` stores the prospect's project_id (not the Sable client org_id). Documents single-operator assumption and migration path for future multi-tenant support.

### A4 — skip_if exception behavior untested
> **Resolved:** Test `test_skip_if_exception_executes_step` added to `tests/workflows/test_engine.py`. Verifies that a `skip_if` lambda that raises `KeyError` (missing ctx.input_data key) causes the step to execute (not skip), per engine.py lines 313–317.

### A6 — list_prospect_scores() has no Sable client filter
> **Resolved (documentation):** Documented in CLAUDE.md "Prospect Scores Schema Note" section. `list_prospect_scores()` returns all prospects globally; this is intentional for the current single-operator model. Full fix (add `client_org_id` column + filter) is blocked by T3-AUTH.

---

## B-series — 2026-04-05 Deep-Dive Audit (all resolved)

### B1 — onboard_client.py: raw sqlite3.Error from _create_initial_sync_record
> **Resolved:** `_create_initial_sync_record()` in `onboard_client.py` now wraps the INSERT + commit + `last_insert_rowid()` in `try/except sqlite3.Error as exc: raise SableError(INVALID_CONFIG, ...)`. Import `sqlite3` added. Test `test_create_initial_sync_record_raises_sable_error_on_db_failure` in `tests/workflows/test_deepaudit_fixes.py` verifies `SableError` (not raw `sqlite3.Error`) is raised on DB failure. 6 tests total in that file.

### B2 — alert_delivery.py: Telegram bot token leakage in log on HTTP failure
> **Resolved:** `_send_telegram()` now catches `urllib.error.HTTPError` and `urllib.error.URLError` explicitly before the generic `except Exception`. HTTPError logs only `f"HTTP {e.code}"`. URLError logs only `f"URLError: {e.reason}"`. Neither case can expose the bot token URL. `import urllib.error` added. Tests `test_send_telegram_http_error_does_not_log_token` and `test_send_telegram_url_error_does_not_log_token` verify token does not appear in caplog output.

### B3 — weekly_client_loop.py: silent return [] when artifact path is NULL
> **Resolved:** `_parse_actions_from_artifact()` now emits `log.warning("No artifact path for %s (org %s, run %s) — zero actions registered", ...)` before the silent `return []`. Test `test_parse_actions_warns_on_missing_artifact_path` verifies warning is logged when artifact row exists with NULL path.

### B4 — connection.py: migration 027 bulk auto-fail with no operator log
> **Resolved:** `_warn_migration_027_autofails(conn)` added to `connection.py` and called after migration 027 is applied in `ensure_schema()`. Queries count of auto-failed runs and emits `log.warning("Migration 027: auto-failed %d duplicate active workflow run(s)", n)` when count > 0. Tests `test_migration_027_warns_on_autofailed_runs` and `test_migration_027_no_warning_when_no_autofails` cover both paths.

---

## A5 — Threshold Configurability (2026-04-05)

### A5-THRESHOLD-CONFIGURABILITY — Per-org alert staleness thresholds
> **Resolved:** `_check_tracking_stale()`, `_check_discord_pulse_stale()`, and `_check_stuck_runs()` in `alert_checks.py` now read threshold overrides from `org.config_json` before falling back to the module constants (`TRACKING_STALE_DAYS=14`, `DISCORD_PULSE_STALE_DAYS=7`, `STUCK_RUN_THRESHOLD_HOURS=2`). Config keys: `tracking_stale_days`, `discord_pulse_stale_days`, `stuck_run_threshold_hours`. Each read is guarded by try/except with `log.warning` fallback. No migration needed. 9 tests in `tests/alerts/test_threshold_configurability.py` verify override-raises-threshold (suppresses), override-lowers-threshold (fires sooner), and no-config fallback for all three thresholds.

---

## T3-STRUCTURED + T3-DR (2026-04-05)

### T3-STRUCTURED — Structured logging extras in engine hot path
> **Resolved:** All `logger.info` and `logger.warning` calls in `engine.py` now pass `extra={"run_id": ..., "org_id": ..., "step_name": ...}` where those values are in scope. Covered: stale-lock auto-fail, orphaned-step auto-fail, corrupt output_json skip, skip_if raise, step completed, step skipped, step failed (both exception and returned-failed paths), workflow completed. `StructuredFormatter` already serialises `extra` keys into JSON — no formatter changes needed. 927 tests pass.

### T3-DR — Disaster recovery runbook
> **Resolved:** `docs/DISASTER_RECOVERY.md` created. Covers 7 sections: backup/restore (manual + cron, integrity check), DB corruption (detect, WAL recovery, restore path), migration rollback (forward-only policy, backup restore, migration 027 auto-fail recovery), cron recovery (re-add presets, verify alert evaluation, manual evaluate), stuck/orphaned runs (identify, force-fail, resume after crash), data retention GC, and a health check quick reference with all relevant CLI commands.

---

## C-series — Codex Review Fixes (2026-04-05)

### DIAG-ORG — prospect_diagnostic_sync silent wrong-org path
> **Resolved:** `_validate_prospect()` now raises `SableError(INVALID_CONFIG)` when `sable_org` is non-empty and doesn't match `ctx.org_id`. Empty/absent `sable_org` passes through (valid — means no platform sync configured). Error message names both values and references the Cult Grader silent-skip risk. 3 tests in `tests/workflows/test_prospect_diagnostic_sync.py`: mismatch fails before adapter is called, absent passes, matching passes.

### ONBOARD-LI — onboard_client missing LeadIdentifier check
> **Resolved:** `_verify_lead_identifier_adapter()` step added to `onboard_client`. Checks `SABLE_LEAD_IDENTIFIER_PATH` env var and path existence using same non-blocking pattern as other adapters. `_mark_complete` loop updated to include `("lead_identifier_adapter", "lead_identifier")`. Step inserted before `create_initial_sync_record`. `test_onboard_client_completes_all_adapters_missing` updated to assert 4 `tools_failed`; `test_onboard_client_includes_lead_identifier_in_tools_failed` added.

### PREFLIGHT-ADAPTERS — workflow preflight is DB-local only
> **Resolved:** `workflow preflight` now runs a 5th check (suite-level, outside per-org loop) verifying all 4 adapter env vars. If set to a path that doesn't exist → `FAIL: suite — adapter_{label}`. Unset env vars are not a failure. 3 tests in `tests/cli/test_preflight.py`: missing path fails, unset is OK, valid path passes.

### DOC-PARITY — CLI_REFERENCE.md drift
> **Resolved:** CLI_REFERENCE.md init section updated from "26 migrations" → "27 migrations". Schema section updated to reflect that default output is `docs/schemas/` (not stdout); `--stdout` flag documented. `tests/test_doc_parity.py` created with 2 tests: migration count matches `len(_MIGRATIONS)`, and `schema --help` mentions both `--stdout` and `docs/schemas`.

---

## Production Hardening Batch (2026-04-05) — 958 tests

### Migration 028 — platform_meta key-value table
> **Resolved:** `028_platform_meta.sql` creates `platform_meta (key PK, value, updated_at)`. Added to `_MIGRATIONS`. All hardcoded `version == 27` assertions updated to `28` across test_migrations.py, test_connection.py, test_health.py, test_init.py. CLI_REFERENCE.md updated to "28 migrations".

### T3-SELFMON — Alert evaluator dead-man's switch
> **Resolved:** `evaluate_alerts()` writes `last_alert_eval_at` heartbeat to `platform_meta` on every run (upsert). `check_db_health()` now returns `last_alert_eval_age_hours` (float|None) and `alert_eval_stale` (bool, True if >26h or never run). 5 tests in `tests/alerts/test_selfmon.py`.

### T3-RATELIMIT — Bounded webhook dispatch
> **Resolved:** `dispatch.py` refactored. New `_http_deliver(url, secret, body_bytes) -> (bool, str)` performs the HTTP call with `timeout=1`. `dispatch_event()` fans out deliveries via `ThreadPoolExecutor(max_workers=4)`, collects results, then writes success/failure to DB serially. Alert eval latency is now bounded by max(4 parallel deliveries) × 1s rather than N_subs × 1s. Timeout test updated to inspect `_http_deliver`. 2 new tests in `tests/webhooks/test_dispatch.py`.

### YAML contract normalization — project_name canonical field
> **Resolved:** `_validate_prospect()` normalizes `name` and `project_slug` aliases to `project_name` before validation. Error message uses canonical field name. `docs/CROSS_REPO_INTEGRATION.md` updated to document `project_name` as canonical. 4 tests in `tests/workflows/test_prospect_diagnostic_sync.py`.

### SP-OBS Phase 2 — /health HTTP endpoint
> **Resolved:** `sable_platform/http_health.py` provides `_HealthHandler` (stdlib BaseHTTPRequestHandler) and `serve_health(port=8765)`. `sable-platform health-server [--port N]` CLI command added. GET /health → 200 JSON (check_db_health output). Any other path → 404. 2 tests in `tests/test_http_health.py`. CLI_REFERENCE.md updated.

### SP-OBS Phase 3 — Prometheus metrics export
> **Resolved:** `sable_platform/metrics.py` exports Prometheus text format via `export_metrics(conn)`. Metrics: `sable_active_orgs`, `sable_workflow_runs_total{status}`, `sable_alerts_total{severity,status}`, `sable_last_alert_eval_age_seconds`. `sable-platform metrics` CLI command added. No new dependencies (stdlib only). 4 tests in `tests/test_metrics.py`. CLI_REFERENCE.md updated.

### SABLE_OPERATOR_ID enforcement (warning only — superseded by T3-AUTH below)
> **Resolved (initial):** `cli/main.py` CLI group callback warned via `log.warning()` when `SABLE_OPERATOR_ID` is unset or "unknown". `tests/conftest.py` sets `os.environ.setdefault("SABLE_OPERATOR_ID", "test")` so existing CLI tests are unaffected.
> **Superseded 2026-04-05 by T3-AUTH:** warning upgraded to hard exit — see T3-AUTH section below.

### T3-INTEGRATION — Adapter contract snapshot tests
> **Resolved:** `tests/integration/fixtures/` contains frozen JSON fixtures for all 4 adapters. `tests/integration/test_adapter_contract_snapshots.py` validates each fixture through the real adapter `get_result()` parse path (not hand-mapped): `CultGraderAdapter.get_result()` via tmp_path fixture files; `LeadIdentifierAdapter.get_result()` via tmp_path + env monkeypatch; `TrackingMetadata.model_validate()` for tracking; `Artifact.model_validate()` for slopper. 4 tests — if adapter normalization logic drifts, these break. Updated 2026-04-05 to use real parse paths.

---

## T3-AUTH + Contract Hardening (2026-04-05) — 968 tests

### Migration 029 — prospect_score_fields
> **Resolved:** `029_prospect_score_fields.sql` added to `_MIGRATIONS`. All version assertions (test_migrations.py, test_connection.py, test_health.py, test_init.py, CLI_REFERENCE.md) updated to 29.

### T3-AUTH — HTTP Bearer token auth for health server
> **Resolved:** `http_health.py` now requires `SABLE_HEALTH_TOKEN` Bearer token on every request. `serve_health()` raises `RuntimeError` immediately if `SABLE_HEALTH_TOKEN` is not set in env — server refuses to start without it (fail closed). `do_GET()` returns 401 with `WWW-Authenticate: Bearer realm="sable-platform"` on missing or wrong token. Token set on `_HealthHandler._token` class var by `serve_health()` before binding. 3 new tests in `tests/test_http_health.py` (401 no auth, 401 wrong token, RuntimeError on missing env var); 2 existing tests updated to pass correct token.
> **Manual cutover:** `export SABLE_HEALTH_TOKEN=$(openssl rand -hex 32)` then restart `sable-platform health-server`.

### T3-AUTH — CLI operator identity enforcement (fail closed)
> **Resolved:** `cli/main.py` group callback upgraded from `log.warning()` to `sys.exit(1)` when `SABLE_OPERATOR_ID` is unset or `"unknown"`. Only the `init` subcommand is exempt (bootstrap must work before identity is configured). `@click.pass_context` added to group. 2 new tests in `tests/cli/test_init.py`: `test_cli_requires_operator_id` verifies exit_code=1 with monkeypatched missing env var; `test_cli_init_exempt_from_operator_id` verifies init succeeds without it.

### Prospect contract — project_name slug fallback in CultGraderAdapter
> **Resolved:** `_parse_latest_run()` in `cult_grader.py` now includes `project_name` as a fallback when resolving the checkpoint directory slug (after `project_slug`/`slug`, before "unknown"). Fixes silent checkpoint miss when YAML uses only the canonical `project_name` field per current docs. 1 new test in `tests/adapters/test_cult_grader.py`: `test_parse_latest_run_project_name_fallback` verifies checkpoint is found when YAML has only `project_name`.

### prospect_scores.org_id semantics documented
> **Resolved:** New section "prospect_scores.org_id — Prospect Project Identifier" added to `docs/SCHEMA_CONTRACTS.md`. Explains the column stores the prospect's `project_id` (not Sable client org_id), documents `graduate_prospect`/`reject_prospect` usage, single-operator assumption, and the migration needed if multi-tenant support is added.

### Cross-repo TODO correction
> **Resolved:** SablePlatform TODO.md was showing P7-1/P7-2/P7-3 and F-REJECT-3 as pending. All were already shipped: P7-1/P7-2/P7-3 in SableTracking `platform_sync.py` (in SableTracking AUDIT_HISTORY), `pull-feedback` in Lead Identifier §9A (2026-04-04), `RelationshipGraph.tsx` and DATA-7 in SableWeb (2026-04-04/05). All cross-repo integration items now marked complete in SablePlatform TODO.
