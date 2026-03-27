# SablePlatform Implementation Log

> Append-only. Each entry = one slice. Most recent first.

---

## Session 2026-03-26 — Adversarial v0.3 hardening pass

### Migration 013: alert_delivery_error
**Files touched:** `sable_platform/db/migrations/013_alert_delivery_error.sql` (new), `sable_platform/db/connection.py`
**What changed:**
- New migration adds `alerts.last_delivery_error TEXT` (nullable, NULL by default)
- `_MIGRATIONS` extended to version 13

### Alert delivery failure tracking
**Files touched:** `sable_platform/db/alerts.py`, `sable_platform/workflows/alert_delivery.py`, `tests/alerts/test_alerts.py`, `tests/db/test_migrations.py`
**What changed:**
- `mark_delivery_failed(conn, dedup_key, error)` added to `db/alerts.py` — stamps `last_delivery_error` (truncated to 500 chars)
- `mark_delivered` updated to also clear `last_delivery_error = NULL` on success
- `_send_telegram` / `_send_discord` now return `str | None` (error string or None)
- `_deliver` collects errors and calls `mark_delivery_failed` vs `mark_delivered` accordingly
**Tests added:** 4 (mark_delivery_failed sets error, truncates at 500, deliver stamps error on discord failure, deliver clears error on success)

### alert_evaluator.py structural split
**Files touched:** `sable_platform/workflows/alert_evaluator.py` (rewritten), `sable_platform/workflows/alert_checks.py` (new), `sable_platform/workflows/alert_delivery.py` (new), `tests/workflows/test_alert_split.py` (new)
**What changed:**
- `alert_evaluator.py` → thin orchestrator (~30 lines)
- New `alert_checks.py` — all 9 `_check_*` functions + 4 constants (`TRACKING_STALE_DAYS`, etc.)
- New `alert_delivery.py` — `_deliver`, `_send_telegram`, `_send_discord`
**Tests added:** 5 (module imports, all checks in checks module, _deliver not in evaluator, end-to-end, per-org failure isolation)

### Per-org failure isolation
**Files touched:** `sable_platform/workflows/alert_evaluator.py`
**What changed:** `evaluate_alerts()` outer loop now has per-org try/except with `log.error` + continue; one bad org no longer aborts remaining orgs

### New CLI commands
**Files touched:** `sable_platform/cli/main.py`, `sable_platform/cli/workflow_cmds.py`, `sable_platform/cli/alert_cmds.py`, `sable_platform/cli/org_cmds.py`, `sable_platform/cli/action_cmds.py`
**What changed:**
- `sable-platform init [--db-path PATH]` — bootstraps DB via `get_db()`, prints resolved path + schema version
- `sable-platform workflow cancel <run_id>` — calls `cancel_workflow_run()`, marks non-terminal run cancelled
- `--json` flag on: `workflow list`, `workflow status`, `alerts list`, `org list`, `actions list`
**Tests added:** 4 (init) + 7 (json flags) + 24 (CLI smoke tests across 6 command groups) = 35

### Documentation
**Files touched:** `CLAUDE.md`, `README.md`, `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_REPORT.md`, `docs/IMPLEMENTATION_LOG.md`
**What changed:** All docs updated to reflect v0.3 state — schema 13, 221 tests, correct module split, new CLI surface

**Tests: 133 → 221 (+88). All pass.**

---

## Session 2026-03-26 — Ruthless conductor pass

### S-01 — CLAUDE.md: document cooldown + version-check + fix current state
**Files touched:** `CLAUDE.md`
**What changed:**
- Updated Current State from v0.1 to v0.2
- Corrected test count (40 → 133), workflow count (2 → 5)
- Added complete CLI surface listing
- Added "Alert Delivery Cooldown" section: semantics, NULL/zero/default rules
- Added "Workflow Config Versioning" section: fingerprint algorithm, `--ignore-version-check` escape hatch
**Tests added:** none (docs only)
**QA criticisms:** none
**Remaining risks:** none

---

### S-02 — TODO.md: mark all completed items done
**Files touched:** `TODO.md`
**What changed:**
- Marked 4 open Feature items ✅ with resolution notes:
  - Client Onboarding Workflow (with spec-divergence note)
  - Alert Cooldown + Discord Pulse Stale Guard
  - Stuck Workflow Run Alert
  - Workflow Config Versioning
- Marked 5 open Simplify items ✅ with resolution notes:
  - `_repo_path()` dedup
  - Magic constants
  - Inline `import json` in jobs.py
  - `_SEVERITY_RANK` dead constant
  - Deferred import + bare except
**Tests added:** none (docs only)
**QA criticisms:** none
**Remaining risks:** `onboard_client` spec divergence documented; no change made to behavior

---

### S-03 — docs/TODO_product_review.md: mark Next Round Features done
**Files touched:** `docs/TODO_product_review.md`
**What changed:**
- Added "Status as of 2026-03-26 implementation session" banner noting all three features done
- Section headers for Feature 1, 2, 3 prefixed with ✅
**Tests added:** none (docs only)
**QA criticisms:** none
**Remaining risks:** none

---

## Session 2026-03-26 — Three Next Round Features (prior pass)

### Alert Cooldown + Discord Pulse Stale Guard (migration 011)
**Files touched:**
- `sable_platform/db/migrations/011_alert_cooldown.sql` (new)
- `sable_platform/db/connection.py`
- `sable_platform/db/alerts.py`
- `sable_platform/workflows/alert_evaluator.py`
- `tests/alerts/test_alerts.py`
- `tests/db/test_migrations.py`
**Tests added:** 4 cooldown + 3 pulse stale = 7 tests
**Key decisions:**
- `_deliver()` signature change is keyword-only (`dedup_key=None`) — all existing call sites
  work unchanged; only sites that should respect cooldown pass the key
- Cooldown checks `last_delivered_at` across ALL statuses for the dedup_key (not just 'new')
  so acknowledge/resolve cycles don't reset the window
**QA criticisms addressed:**
- Verified all 7 `_deliver()` call sites updated with dedup_key
- `cooldown_hours=0` tested to confirm disables cooldown

---

### Stuck Workflow Run Alert (no migration)
**Files touched:**
- `sable_platform/workflows/alert_evaluator.py`
- `tests/alerts/test_alerts.py`
**Tests added:** 3 tests
**Key decisions:**
- Uses `started_at` not `updated_at` (workflow_runs doesn't have updated_at)
- Registered in per-org loop, not the cross-org `_check_workflow_failures` pattern

---

### Workflow Config Versioning (migration 012)
**Files touched:**
- `sable_platform/db/migrations/012_workflow_version.sql` (new)
- `sable_platform/db/connection.py`
- `sable_platform/db/workflow_store.py`
- `sable_platform/workflows/engine.py`
- `sable_platform/cli/workflow_cmds.py`
- `tests/workflows/test_engine.py`
- `tests/db/test_migrations.py`
**Tests added:** 4 versioning + 2 migration column = 6 tests
**Key decisions:**
- Column named `step_fingerprint` (not `workflow_version`) because `workflow_runs.workflow_version`
  already existed in migration 006 storing the definition's `.version` string
- NULL stored fingerprint = skip check (old runs resume safely)
- Fingerprint covers step names only (sorted), not step logic — structural changes only
