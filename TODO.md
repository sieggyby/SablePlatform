# SablePlatform — Canonical Roadmap

Items are ordered by execution priority within each tier. P1 fixes data integrity and correctness
risks that affect production use. P2 is quality-of-life improvements that become increasingly
painful to defer as the DB grows and client count increases. Features are gated behind P1 complete.

See CLAUDE.md for project architecture, key files, and working conventions.

---

## Priority Summary

| Tier | What it covers |
|---|---|
| P1 | Data integrity, silent correctness failures, security-adjacent risks |
| P2 | Performance, maintainability, anti-patterns that compound over time |
| P3 | Cosmetic / misleading but not harmful |
| Feature | Net-new capability; requires P1 complete |
| Simplify | Refactors that reduce surface area with zero behavior change |

---

## P1 — Data Integrity and Correctness

---

### ✅ P1-1 — redact_error() not called before persisting step error messages

> **Resolved:** Both `fail_workflow_step` and `fail_workflow_run` in `workflow_store.py` call `redact_error()` at the SQL parameter site. Test: `test_redact_error_in_step_failure` passes.

---

### ✅ P1-2 — skipped-step output_json uses "reason" key, polluting accumulated context

> **Resolved:** `skip_workflow_step()` writes `{"_skip_reason": reason}`. Test: `test_skip_reason_does_not_overwrite_prior_output` passes.

---

## P2 — Quality and Maintainability

---

### ✅ P2-1 — adapter status()/get_result() open new DB connections per call
> **Resolved:** `conn=None` + `_owns` pattern already in `SableTrackingAdapter` and `SlopperAdvisoryAdapter`. Tests: `test_tracking_adapter_status_uses_provided_conn`, `test_tracking_adapter_get_result_uses_provided_conn`, `test_slopper_adapter_status_uses_provided_conn`, `test_slopper_adapter_get_result_uses_provided_conn` pass.

---

### ✅ P2-2 — _check_workflow_failures scans all historical failed runs without a time window
> **Resolved:** Query already filters `created_at > datetime('now', '-30 days')`. Tests: `test_old_workflow_failure_does_not_alert`, `test_recent_workflow_failure_does_alert` pass.

---

### ✅ P2-3 — open() used without context manager in _register_actions (weekly_client_loop.py)
> **Resolved:** `weekly_client_loop.py` already uses `with open(...) as fh:` context manager.

---

### ✅ P2-4 — Stale test name in test_migrations.py
> **Resolved:** Test already named `test_fresh_db_reaches_current_version()`, asserting version 9 (current head).

---

## Features (gated behind P1 complete)

P1 is complete. Implement in order: **Alert Delivery → Client Onboarding**. Current schema head is migration 009 (`009_alerts.sql`); the next migration file is `010_alert_delivery.sql`.

---

### Feature: Entity Interaction Edge Table (prerequisite for relationship web visualization)

**Context:** SableWeb portal has a vision for a relationship web — a graph visualization showing how community members are connected (who replies to whom, who co-mentions whom, cluster boundaries). This would make the difference between hub-and-spoke and genuine rhizome communities immediately visible in client presentations. Currently `lateral_reply_pairs` is just an aggregate count; no edge data exists in sable.db.

**What this feature adds:**

A new `entity_interactions` table (new migration) storing directional interaction edges between entities:
```sql
CREATE TABLE entity_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT NOT NULL,
    source_handle TEXT NOT NULL,   -- the entity initiating the interaction
    target_handle TEXT NOT NULL,   -- the entity being replied to / mentioned
    interaction_type TEXT NOT NULL, -- "reply" | "mention" | "co_mention"
    count INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT,               -- ISO date of earliest observed interaction
    last_seen TEXT,                -- ISO date of most recent observed interaction
    run_date TEXT,                 -- diagnostic run that sourced this edge
    FOREIGN KEY (org_id) REFERENCES orgs(org_id)
);
CREATE INDEX idx_entity_interactions_org ON entity_interactions(org_id);
```

**platform_sync.py additions:**
- `sync_interaction_edges(conn, org_id, edges, run_date)` — upserts edges from Cult Grader's `computed_metrics.json` when a `reply_pairs` field is present. Idempotent: update count + last_seen on conflict, insert on new edge.
- Called as part of `sync_after_run()` if `reply_pairs` key exists in computed metrics.

**CLI surface:**
- `sable-platform inspect interactions --org ORG [--type reply|mention] [--min-count N]` — list top edges for an org, sorted by count descending.

**Dependency:** Cult Grader Stage 4 (metric_computation) must extract individual reply pairs before this table has data to store. See `Sable_Cult_Grader/TODO.md` — Post-MVP: Extract individual reply pairs for relationship graph.

**Not in scope here:** The graph rendering itself lives in SableWeb. This feature only provides the data layer.

**See also:** `SableWeb/docs/TODO_product_review.md` — Session 4 Addendum, Relationship Web Vision.

---

### ✅ Feature: Alert Delivery via Telegram/Discord
> **Resolved:** `_deliver()`, `_send_telegram()`, `_send_discord()` already implemented in `alert_evaluator.py` using `urllib.request`. Columns `telegram_chat_id` / `discord_webhook_url` already in `alert_configs` (migration 009). Token read from `SABLE_TELEGRAM_BOT_TOKEN` env var (documented in CLAUDE.md). Tests: `test_telegram_delivery_called_when_configured`, `test_telegram_delivery_failure_does_not_propagate`, `test_discord_delivery_failure_does_not_propagate` pass.

---

### ✅ Feature: Client Onboarding Workflow (onboard_client builtin)
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

---

### ✅ Feature: Workflow Run Garbage Collection (sable-platform gc)
> **Resolved:** `sable-platform gc [--hours N]` implemented in `workflow_cmds.py`. `mark_timed_out_runs()` in `workflow_store.py`. Tests: `test_gc_marks_stuck_run_timed_out`, `test_gc_ignores_recent_run`, `test_gc_is_idempotent` pass.

---

### ✅ Feature: Alert Cooldown + Discord Pulse Stale Guard
> **Resolved:** Migration 011 adds `alert_configs.cooldown_hours INTEGER DEFAULT 4` and
> `alerts.last_delivered_at TEXT`. `_deliver()` in `alert_evaluator.py` now accepts `dedup_key`
> (keyword-only), checks `last_delivered_at` against `cooldown_hours`, suppresses HTTP delivery
> if within window, stamps `mark_delivered()` after successful delivery. All 7 `_deliver()` call
> sites updated with `dedup_key`. `_check_discord_pulse_stale()` fires `warning/discord_pulse_stale`
> when no pulse data in last 7 days. DB helpers `get_last_delivered_at()` and `mark_delivered()`
> added to `db/alerts.py`. Tests: 4 cooldown cases + 3 pulse stale cases pass.
> Migration version: 11. CLAUDE.md updated with cooldown semantics.

---

### ✅ Feature: Stuck Workflow Run Alert
> **Resolved:** `_check_stuck_runs()` added to `alert_evaluator.py`. Queries `workflow_runs`
> for `status='running'` AND `started_at < datetime('now', '-2 hours')`. Fires `warning/stuck_run`
> per stuck run with `dedup_key=f"stuck_run:{run_id}"`. `STUCK_RUN_THRESHOLD_HOURS = 2` constant.
> Registered in `evaluate_alerts()` inside per-org loop. Try/except guard. No migration.
> Tests: `test_stuck_run_fires_warning`, `test_recent_run_no_alert`,
> `test_timed_out_run_no_double_alert` pass.

---

### ✅ Feature: Workflow Config Versioning
> **Resolved:** Migration 012 adds `workflow_runs.step_fingerprint TEXT` (nullable). `_workflow_fingerprint()`
> in `engine.py` computes `sha1(sorted_step_names)[:8]`. `run()` stores fingerprint via
> `create_workflow_run(step_fingerprint=fp)`. `resume()` compares stored vs current fingerprint;
> raises `SableError(STEP_EXECUTION_ERROR)` on mismatch; skips check if stored is NULL (old runs).
> `--ignore-version-check` flag added to `sable-platform workflow resume`. `workflow_store.py`
> `create_workflow_run()` accepts optional `step_fingerprint`. Tests: `test_version_mismatch_raises`,
> `test_null_version_skips_check`, `test_matching_version_resumes`, `test_ignore_version_check_bypasses_error`
> pass. CLAUDE.md updated with versioning semantics and escape hatch.

---

## Simplify (zero behavior change, reduces surface area)

---

### ✅ Simplify: _repo_path() duplicated across 4 adapter files
> **Resolved:** `SubprocessAdapterMixin._resolve_repo_path(env_var)` implemented in `adapters/base.py`.
> All 4 adapters use it as a one-liner: `return self._resolve_repo_path("ENV_VAR_NAME")`.
> Error message names the env var. Exception type and code unchanged.

---

### ✅ Simplify: Magic constants in merge.py, entities.py, and alert_evaluator.py
> **Resolved:** `MERGE_CONFIDENCE_THRESHOLD = 0.70` in `merge.py`,
> `SHARED_HANDLE_MERGE_CONFIDENCE = 0.80` in `entities.py`,
> `TRACKING_STALE_DAYS = 14` in `alert_evaluator.py`. All module-top named constants.

---

### ✅ Simplify: Inline import json in jobs.py (3 locations)
> **Resolved:** `import json` is at module top in `sable_platform/db/jobs.py`. No inline imports.

---

### ✅ Simplify: _SEVERITY_RANK dead constant in db/alerts.py
> **Resolved:** `_SEVERITY_RANK` is not present in `sable_platform/db/alerts.py` (already removed
> or never added). Grep confirms zero references across codebase.

---

### ✅ Simplify: Deferred import and bare except in cost.py and entities.py
> **Resolved:**
> (a) `from sable_platform.errors import SableError, BUDGET_EXCEEDED` is at module top in
> `sable_platform/db/cost.py`. Deferred `import yaml` inside `_read_platform_config()` retained
> (intentional optional-dependency guard).
> (b) `add_handle()` in `entities.py` uses `except sqlite3.IntegrityError: pass` — typed,
> not bare. Only UNIQUE constraint violations are silenced; all other errors propagate.
