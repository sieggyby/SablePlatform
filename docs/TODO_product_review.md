# SablePlatform Product Review — 2026-03-26

> Multi-agent simulation: PM Council → QA Review Board → Judge → Salvage → Final Recommendation.
> Results implemented in this session. Deferred items listed at end.

---

## Executive Summary

Three features selected and implemented from a full PM/QA/Judge simulation on the SablePlatform
codebase (107 tests → 117 tests passing).

| Priority | Feature | Files | Tests Added |
|---|---|---|---|
| P1 | Discord pulse regression alerts | `alert_evaluator.py` | 3 |
| P2 | Client health dashboard CLI | `inspect_cmds.py` | 4 |
| Stretch | Alert mute / unmute | `alert_cmds.py` | 3 |

---

## Review Process

### Repo Recon (state at review time)

- 10 DB migrations (001–010), 25+ tables
- 5 builtin workflows, 4 subprocess adapters, 6 CLI command groups
- Alert pipeline: `evaluate_alerts()` → 5 `_check_*` functions → `_deliver()` → Telegram/Discord
- `discord_pulse_runs` table fully migrated and queryable, but **no alert check read from it** — F-DM loop half-open
- No unified health-at-a-glance CLI command; operators needed 4–5 separate queries per shift
- No alert mute/unmute; only full disable via `alerts config set --disable`

### PM Council Nominees

- **PM-1:** Client health dashboard CLI (`inspect health <org_id>`) — daily operator tool
- **PM-2:** Alert suppression windows (temporal `suppressed_until` column) — reduces maintenance noise
- **PM-3:** Discord pulse regression alerts — closes the F-DM feedback loop at zero infrastructure cost

### QA Review Results

| Finalist | Logic Risk | Arch Fit | Code Quality | Product Value | Score |
|---|---|---|---|---|---|
| Health dashboard CLI | LOW | CLEAN | HIGH | STRONG | **91/100** |
| Alert suppression windows | MEDIUM-HIGH | MODERATE | MEDIUM | PREMATURE | **67/100** |
| Discord pulse regression | LOW | CLEAN | HIGH | STRONG | **92/100** |

QA-2 flagged alert suppression windows: non-trivial edge case where alerts firing during a
suppression window are permanently missed if the cron gap spans the window boundary.

### Judge Decision

**Winner: Discord pulse regression alerts (92/100).** Zero new infrastructure. Zero migrations.
Directly closes the F-DM feedback loop that migration 010 opened.

**Runner-up: Client health dashboard CLI (91/100).** Second by 1 point; implemented as P2.

**Eliminated: Alert suppression windows.** Premature at current scale; non-trivial temporal-state
edge case; lower ROI.

### Salvage Round

Alert suppression was simplified to **alert mute/unmute**: plain toggle on
`alert_configs.enabled`, no timestamp, no temporal logic. Salvaged as stretch item.

---

## Implemented Features

### P1 — Discord Pulse Regression Alerts

**File:** `sable_platform/workflows/alert_evaluator.py`

Added:
- `DISCORD_PULSE_REGRESSION_THRESHOLD = 0.05` constant
- `_check_discord_pulse_regression(conn, org_id) -> list[str]` — fires `warning` alert when
  `discord_pulse_runs.retention_delta < -0.05` for any recent run
- Wired into `evaluate_alerts()` alongside other per-org checks
- NULL guard: no alert when `retention_delta IS NULL` (first run, no prior week to compare)
- Dedup key: `discord_pulse_regression:{org_id}:{project_slug}:{run_date}`

**Tests added:** `tests/alerts/test_alerts.py`
- `test_discord_pulse_regression_alert_fires` — drop of 0.07 triggers warning
- `test_discord_pulse_regression_skips_null_delta` — NULL delta → no alert
- `test_discord_pulse_regression_skips_positive_delta` — improvement → no alert

---

### P2 — Client Health Dashboard CLI

**File:** `sable_platform/cli/inspect_cmds.py`

Added `sable-platform inspect health <org_id>` with four sections:

1. **Sync Freshness** — last completed sync per `sync_type`, age in days
2. **Open Alerts** — count by severity (critical / warning / info)
3. **Discord Pulse (latest)** — run_date, wow_retention_rate, echo_rate, weekly_active_posters,
   retention_delta
4. **Recent Workflows** — last 5 runs: name, status, started_at

`--json` flag emits a machine-readable dict for scripting.

Graceful handling: org not found → error message, no crash; zero data → "(none)" labels.

**Tests added:** `tests/cli/test_inspect_health.py`
- `test_health_org_not_found` — graceful exit
- `test_health_no_data` — zero-data labels present
- `test_health_full_output` — all sections populated
- `test_health_json_flag` — valid JSON with expected keys

---

### Stretch — Alert Mute / Unmute

**File:** `sable_platform/cli/alert_cmds.py`

Added:
- `sable-platform alerts mute <org_id>` — sets `alert_configs.enabled = 0`
- `sable-platform alerts unmute <org_id>` — sets `alert_configs.enabled = 1`

Both commands create the `alert_configs` row if it doesn't exist. Targeted `UPDATE`/`INSERT`
— does not reset `min_severity`, `telegram_chat_id`, or `discord_webhook_url`.

**Tests added:** `tests/cli/test_alert_mute.py`
- `test_mute_sets_enabled_false`
- `test_unmute_sets_enabled_true`
- `test_mute_suppresses_alert_delivery` — muted org gets no Telegram/Discord calls

---

## Next Round — Queued Features

> **Status as of 2026-03-26 implementation session:** All three Next Round Features are implemented.
> Tests: 133 passing (up from 117 at review time).

> Cross-repo audit (2026-03-26) confirmed zero overlap with Slopper, Cult Grader, SableTracking,
> and Lead Identifier TODOs. All three are SablePlatform-internal concerns safe to implement.
> Implement in order: Features 1+2 in one pass (shared migration + same file), Feature 3 after.

---

### ✅ Next Round Feature 1 — Alert Cooldown / Delivery Dedup Window

**Why:** `_deliver()` fires Telegram/Discord on every `evaluate_alerts()` call that finds a
matching condition. The existing `dedup_key` prevents duplicate DB records but not repeated
delivery after acknowledge/resolve cycles. Alert spam causes operators to distrust the system.

**Migration:** `sable_platform/db/migrations/011_alert_cooldown.sql`
```sql
ALTER TABLE alert_configs ADD COLUMN cooldown_hours INTEGER NOT NULL DEFAULT 4;
ALTER TABLE alerts ADD COLUMN last_delivered_at TEXT;
UPDATE schema_version SET version = 11;
```

**New helpers in `sable_platform/db/alerts.py`:**
- `get_last_delivered_at(conn, org_id, dedup_key) -> str | None` — most-recent delivery timestamp
- `mark_delivered(conn, alert_id) -> None` — sets `last_delivered_at = datetime('now')`

**`_deliver()` changes in `sable_platform/workflows/alert_evaluator.py`:**
- Add `DEFAULT_ALERT_COOLDOWN_HOURS = 4` constant
- After severity-rank gate: if same `dedup_key` delivered within `cooldown_hours`, log
  "suppressed (cooldown)" and return — no HTTP call, no mark_delivered
- After `_send_telegram` / `_send_discord`: call `mark_delivered(conn, alert_id)`
- Signature must accept `alert_id` and `dedup_key` — audit and update all `_deliver()` call sites

**Edge cases:**
- `last_delivered_at IS NULL` → treat as never delivered → fire
- `cooldown_hours = 0` → cooldown disabled, always deliver
- Cooldown does NOT reset on acknowledge/resolve — it ages out naturally

**Files:** `011_alert_cooldown.sql` (new), `db/connection.py`, `db/alerts.py`,
`workflows/alert_evaluator.py`, `tests/alerts/test_alerts.py` (+7 tests),
`tests/db/test_migrations.py` (version assertion → 11, column-existence test), `CLAUDE.md`

---

### ✅ Next Round Feature 2 — Discord Pulse Stale Guard

**Why:** Migration 010 created `discord_pulse_runs` but no check in `alert_evaluator.py`
references it. If the F-DM ingestion pipeline breaks, the alert system is silently blind to an
entire health dimension. An `info` alert on missing/stale data surfaces this gap.

**No migration needed.** Add to `sable_platform/workflows/alert_evaluator.py`:

```python
DISCORD_PULSE_STALE_DAYS = 7

def _check_discord_pulse_stale(conn, org_id) -> list[str]:
    # Query MAX(run_date) FROM discord_pulse_runs WHERE org_id=?
    # If NULL  → alert_type="discord_pulse_missing", severity="info"
    #            dedup_key=f"discord_pulse_missing:{org_id}"
    # If days_since > DISCORD_PULSE_STALE_DAYS:
    #            alert_type="discord_pulse_stale", severity="info"
    #            dedup_key=f"discord_pulse_stale:{org_id}"
    # Pattern: identical to _check_tracking_stale()
```

Register in `evaluate_alerts()` alongside other per-org checks.

**Files:** `workflows/alert_evaluator.py`, `tests/alerts/test_alerts.py` (+3 tests:
no-rows fires, old-data fires, fresh-data no-alert)

---

### ✅ Next Round Feature 3 — Workflow Config Versioning

**Why:** `resume()` replays steps against the *current* workflow definition with no warning.
If a builtin workflow changes between run creation and resume (step added, renamed, reordered),
the engine silently executes with mismatched semantics. No incident yet, but the blast radius
grows as workflow definitions evolve.

**Migration:** `sable_platform/db/migrations/012_workflow_version.sql`
```sql
ALTER TABLE workflow_runs ADD COLUMN workflow_version TEXT;
UPDATE schema_version SET version = 12;
```

**Engine changes in `sable_platform/workflows/engine.py`:**
- `run()`: compute `sha1("|".join(sorted(step.name for step in workflow.steps)))[:8]`, pass to
  `create_workflow_run()` as `workflow_version`
- `resume()`: recompute fingerprint; if stored version is non-NULL and mismatches current, raise
  `SableError` with message naming both fingerprints and the `--ignore-version-check` escape hatch

**DB change in `sable_platform/db/workflow_store.py`:**
- Update `create_workflow_run()` to accept and persist `workflow_version`

**CLI change in `sable_platform/cli/workflow_cmds.py`:**
- Add `--ignore-version-check` flag to `resume` command; pass through to `runner.resume()`

**Rules:**
- `workflow_version = NULL` (existing runs) → skip validation silently, never block
- Fingerprint covers step *names* only (sorted) — logic changes inside steps are invisible
- Use `hashlib.sha1` (stdlib), no new dependencies
- Document `--ignore-version-check` in `CLAUDE.md`

**Files:** `012_workflow_version.sql` (new), `db/connection.py`, `db/workflow_store.py`,
`workflows/engine.py`, `cli/workflow_cmds.py`, `tests/workflows/test_engine.py` (+4 tests:
mismatch raises, NULL skips check, match resumes cleanly, flag bypasses error), `CLAUDE.md`

---

## Deferred Features

### Deferred-1: Full Discord Pulse Threshold Alerts

Threshold-based alerts on `wow_retention_rate`, `echo_rate`, `avg_silence_gap_hours`. Deferred
pending business decisions on thresholds. When decided: add named constants
`WOW_RETENTION_WARN_THRESHOLD`, `ECHO_RATE_WARN_THRESHOLD` to `alert_evaluator.py` and extend
`_check_discord_pulse_regression()` into a fuller `_check_discord_pulse()`.

### Deferred-2: `_deliver()` Decomposition

After cooldown lands, `_deliver()` will have 5 responsibilities. Decompose into:
- `_should_deliver(conn, org_id, dedup_key, severity) -> bool` — pure predicate
- `_dispatch(token, chat_id, webhook_url, message)` — side effects only

Zero behavior change. Low priority Simplify item.

---

## Verification

```bash
cd /Users/sieggy/Projects/SablePlatform
python3 -m pytest tests/ -x -q
# Expected: 117 passing

# Manual smoke test
sable-platform inspect health <org_id>
sable-platform alerts mute <org_id>
sable-platform alerts unmute <org_id>
```
