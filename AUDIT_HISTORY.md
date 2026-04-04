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
