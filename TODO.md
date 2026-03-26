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

### P1-1 — redact_error() not called before persisting step error messages

**What:** `fail_workflow_step()` and `fail_workflow_run()` in `engine.py` persist the raw exception
message (which may include subprocess stderr snippets) to `workflow_steps.error` in `sable.db`.
`redact_error()` exists in `errors.py` but is never called on this path.

**Why:** Tool subprocess stderr may echo env vars or partial API keys in error messages. AGENTS.md
Tier 1: "subprocess adapter result silently ignored when it signals failure." The same principle
applies to failure output stored in the DB — it must not contain raw credentials.

**Files to touch:**
- `sable_platform/workflows/engine.py` — `fail_workflow_step` and `fail_workflow_run` call sites
- `sable_platform/errors.py` — `redact_error` (already exists; no changes needed)

**Expected outcome:** Any error string written to `workflow_steps.error` passes through
`redact_error()` first. A test asserts that a string containing a known secret pattern is
redacted before the DB write.

**Gotchas / constraints:**
- Do not change the exception type or the signature of `fail_workflow_step` / `fail_workflow_run`.
  The redaction is a one-liner wrapping the error string at the call site, not a refactor of the
  helper signatures.
- `redact_error()` must never raise — confirm this before relying on it. If it can raise, wrap it.

---

### P1-2 — skipped-step output_json uses "reason" key, polluting accumulated context

**What:** `skip_workflow_step()` writes `{"reason": reason_string}` to `output_json`. In
`engine.resume()`, skipped steps' `output_json` is merged into accumulated context via
`accumulated.update()`. This silently overwrites any legitimate `"reason"` key from a prior
step's output.

**Why:** Silent data corruption in accumulated workflow context. A step that outputs
`{"reason": "community gap"}` would be overwritten by a later skipped step. This is a Tier 2
correctness risk per AGENTS.md: "WorkflowRunner not propagating step output into subsequent
ctx.input_data."

**Files to touch:**
- `sable_platform/db/workflow_store.py` — `skip_workflow_step` (change the key it writes)
- `sable_platform/workflows/engine.py` — the `.update()` merge in `resume()`

**Expected outcome:** `skip_workflow_step()` stores `{"_skip_reason": reason}` instead of
`{"reason": reason}`. The leading underscore is a reserved-key convention; callers must not use
`_skip_reason` as an output key. A test asserts that a legitimate `"reason"` key from a prior
step survives across a subsequent skipped step.

**Gotchas / constraints:**
- Any existing tests that assert on `{"reason": ...}` in skipped step output must be updated.
- Check whether any builtin workflow reads `output_json["reason"]` from a skipped step — that
  would be a second bug that needs a coordinated fix.

---

## P2 — Quality and Maintainability

---

### P2-1 — adapter status() and get_result() open new DB connections per call

**What:** `SableTrackingAdapter.status()`, `SableTrackingAdapter.get_result()`,
`SlopperAdvisoryAdapter.status()`, and `SlopperAdvisoryAdapter.get_result()` each call `get_db()`
independently, opening a new SQLite connection per call.

**Why:** Under synchronous workflow execution the engine may call `status()` in a polling loop.
Each call opens a new connection, holding it for the duration of the call. This creates
unnecessary connection churn and can cause lock contention under concurrent adapter calls.

**Files to touch:**
- `sable_platform/adapters/tracking_sync.py`
- `sable_platform/adapters/slopper.py`
- Any other adapters with the same `get_db()` pattern in `status()` / `get_result()`
- `sable_platform/adapters/base.py` — update `AdapterBase` Protocol if it defines these signatures

**Expected outcome:** `status()` and `get_result()` accept an optional `conn` parameter. Callers
that already hold `ctx.db` pass it in; when `conn` is `None` the methods fall back to `get_db()`.
The interface change is backward-compatible — no caller is broken by the addition of an optional
parameter.

**Gotchas / constraints:**
- This is a non-breaking interface change. Do not make `conn` required.
- If `AdapterBase` is a Protocol, update it. If it is an ABC, update it. Do not silently diverge
  the concrete classes from the declared interface.
- Test: assert that when a `conn` is passed, no new `get_db()` call is made.

---

### P2-2 — _check_workflow_failures scans all historical failed runs without a time window

**What:** `_check_workflow_failures()` in `alert_evaluator.py` queries all `workflow_runs WHERE
status='failed'` with no date filter or `LIMIT`. As the DB grows, this becomes an unbounded scan.

**Why:** Performance degrades over time. More critically, very old failed runs that were already
resolved and re-failed will never re-alert because their `dedup_key` was consumed and not
re-scanned after resolve.

**Files to touch:**
- `sable_platform/workflows/alert_evaluator.py` — `_check_workflow_failures` function

**Expected outcome:** The query gains `AND created_at > datetime('now', '-30 days')`. 30 days is
sufficient — any workflow failure persisting for 30 days unresolved is either an operational
choice or a dead run. A test asserts that a 31-day-old failure is not included in results.

**Gotchas / constraints:**
- Confirm the `created_at` column exists on `workflow_runs` and is populated at insert time.
  If it is nullable, the filter must handle `NULL` rows gracefully (they should be included, not
  dropped, since NULL means unknown age — safer to alert than to silently drop).

---

### P2-3 — open() used without context manager in _register_actions (weekly_client_loop.py)

**What:** In `weekly_client_loop.py`, there is a bare
`open(playbook_path, encoding="utf-8").readlines()` call without a `with` statement.

**Why:** Python will eventually close the file handle via garbage collection, but it is not
deterministic. On exception the handle leaks. This is a known Python anti-pattern.

**Files to touch:**
- `sable_platform/workflows/builtins/weekly_client_loop.py` — `_register_actions`, around line 190

**Expected outcome:** The bare `open()` call is replaced with:
```python
with open(playbook_path, encoding="utf-8") as fh:
    lines = fh.readlines()
```
No behavior change; purely a resource safety fix.

**Gotchas / constraints:** Trivial fix. Confirm the variable name used downstream before
replacing; do not rename `lines` or `readlines()` result if it is used further in the function.

---

### P2-4 — Stale test name in test_migrations.py

**What:** `test_fresh_db_reaches_version_6()` in `tests/db/test_migrations.py` asserts that
schema version reaches 9 (current head), but the function name says "version_6".

**Why:** Misleading test name confuses developers about the expected schema version. AGENTS.md
Tier 3: "misleading naming or comments in DB helpers."

**Files to touch:**
- `tests/db/test_migrations.py`

**Expected outcome:** Function renamed to `test_fresh_db_reaches_current_version` (or
`test_fresh_db_reaches_version_9` if the version is expected to stay at 9 for now). Assertion
value confirmed before renaming.

**Gotchas / constraints:** Confirm the assertion value matches the actual current migration head
before renaming. Do not rename and leave a stale assertion value — that would trade one
misleading signal for another.

---

## Features (gated behind P1 complete)

---

### Feature: Alert Delivery via Telegram/Discord

**Priority:** P2 / Feature

**What:** Complete the existing `_deliver()` stub in `alert_evaluator.py` to dispatch alert text
to a configured Telegram chat ID or Discord webhook URL. Currently alerts are only written to
`sable.db`; the delivery mechanism is a documented stub ("v2: send to telegram_chat_id /
discord_webhook_url if configured").

**Why:** Operators currently must poll `sable-platform alerts` to see alerts. Proactive delivery
removes that friction and makes the alert system actionable.

**Files to touch:**
- `sable_platform/workflows/alert_evaluator.py` — `_deliver()` stub
- `sable_platform/db/connection.py` (or wherever `_MIGRATIONS` is defined) — add migration 010

**Implementation notes:**
- Add migration 010 to `_MIGRATIONS` with:
  ```sql
  ALTER TABLE alert_configs ADD COLUMN telegram_chat_id TEXT;
  ALTER TABLE alert_configs ADD COLUMN discord_webhook_url TEXT;
  ```
- Implement HTTP dispatch inside `_deliver()`: send to Telegram via
  `https://api.telegram.org/bot{token}/sendMessage` if `telegram_chat_id` is set; send to
  Discord via the webhook URL if `discord_webhook_url` is set.
- Use `urllib.request` only. Do NOT add `requests` or `httpx` as dependencies.
- The HTTP call must be wrapped in `try/except` that logs on failure and never raises. Delivery
  failure must never abort alert evaluation.
- Tests must mock the HTTP call and verify the severity-rank gate still works correctly —
  low-severity alerts must still be suppressed per existing logic.

**Expected outcome:** When `alert_configs` has a `telegram_chat_id` or `discord_webhook_url`
set, triggered alerts are dispatched via HTTP in addition to being written to `sable.db`. A
delivery failure produces a log line but does not affect the alert record or subsequent
evaluation.

**Gotchas / constraints:**
- No new external dependencies.
- Delivery failure is always non-fatal.
- The Telegram bot token is not owned by this repo — it must come from an env var
  (`SABLE_TELEGRAM_BOT_TOKEN` or similar). Document the required env var in CLAUDE.md.

---

### Feature: Client Onboarding Workflow (onboard_client builtin)

**Priority:** P2 / Feature

**What:** Add a builtin workflow named `onboard_client` that, given `org_id` and tool configs,
runs a structured checklist: verify `sable.db` org exists, verify each tool adapter responds
(subprocess ping), create an initial `sync_run` record, output a readiness report.

**Why:** New client onboarding (Multisynq, PSY Protocol) requires manual validation across 4
tools. A builtin workflow makes this repeatable and auditable.

**Files to touch:**
- New builtin workflow file under `sable_platform/workflows/builtins/`
- `sable_platform/workflows/registry.py` — add to `_auto_register`
- `sable_platform/adapters/` — use existing adapters for tool verification (read-only)

**Implementation notes:**
- Follow the builtin workflow pattern in the existing builtins directory exactly.
- Tool verification must shell out through the existing `SubprocessAdapterMixin`. Do NOT import
  directly from specialized repos.
- The "create initial sync_run record" step must use the proper DB helper with `conn.commit()`.
  Do NOT use an ad-hoc inline `INSERT` that skips commit — this is a known Tier 2 failure mode
  in this codebase (AGENTS.md: "DB helper bypassing conn.commit() or leaving uncommitted state").
- Test with a mocked adapter that returns a non-zero exit code to verify the workflow fails
  gracefully and does NOT write a partial `sync_run` record on failure.
- The readiness report should be a structured dict in the final step's `output_json`, e.g.:
  `{"org_id": ..., "tools_verified": [...], "tools_failed": [...], "sync_run_id": ...}`.

**Expected outcome:** `sable-platform workflow run onboard_client --input '{"org_id": "multisynq"}'`
runs the checklist, writes a `sync_run` record on full success, and outputs a structured
readiness report. On any tool verification failure the workflow halts without a partial
`sync_run` record.

**Gotchas / constraints:**
- If the workflow is not added to `_auto_register` in `registry.py` it will never appear in
  `sable-platform workflow list` (AGENTS.md repo-specific context).
- The org must exist in `sable.db` before the workflow is run. Add a clear error if the org is
  missing rather than creating it silently.

---

### Feature: Workflow Run Garbage Collection (sable-platform gc)

**Priority:** P2 / Feature

**What:** New `sable-platform gc` command that marks `workflow_runs` stuck in `"running"` state
for longer than N hours as `"timed_out"`, and logs affected `run_id`s to stdout with a summary
count to stderr.

**Why:** Partial workflow failures leave runs stuck in `"running"` state permanently, causing
`_check_workflow_failures` in `alert_evaluator.py` to keep scanning them as unresolved. AGENTS.md
flags stuck-running state as a Tier 1 known risk.

**Files to touch:**
- `sable_platform/cli/workflow_cmds.py` — add `gc` subcommand
- `sable_platform/db/workflow_store.py` — add `mark_timed_out_runs(conn, hours: int) -> list[str]` helper

**Implementation notes:**
- CLI: `sable-platform gc [--hours N]` (default N=6).
- Only transition runs currently in `"running"` status, not `"pending"`. Pending = not started;
  stale is a different state.
- Before implementing: verify `workflow_runs.status` has no `CHECK` constraint that would reject
  `"timed_out"`. If a constraint exists, it must be updated in a migration before the column
  value can be written.
- Emit affected `run_id`s one per line to stdout. Emit `"Marked N run(s) as timed_out"` to
  stderr. This split allows stdout to be piped for scripting.
- Test: assert a run with `started_at` older than N hours transitions to `"timed_out"`. Assert
  a run started within N hours is not touched.

**Expected outcome:** After `sable-platform gc`, no `workflow_runs` rows remain in `"running"`
state with `started_at` older than N hours. Each affected `run_id` is printed to stdout.
`_check_workflow_failures` no longer repeatedly alerts on runs that have been gc'd.

**Gotchas / constraints:**
- `gc` must be idempotent — running it twice must produce the same DB state and a count of 0
  on the second run.
- Do not touch `"pending"` runs. If pending runs are also stale, that is a separate concern and
  requires a separate discussion before a fix.

---

## Simplify (zero behavior change, reduces surface area)

---

### Simplify: _repo_path() duplicated across 4 adapter files

**What:** `CultGraderAdapter`, `LeadIdentifierAdapter`, `SlopperAdvisoryAdapter`, and
`SableTrackingAdapter` each define `_repo_path()` with identical logic: read an env var, raise
`SableError(INVALID_CONFIG)` if missing or not a directory, return the `Path`.

**Files to touch:**
- `sable_platform/adapters/cult_grader.py`
- `sable_platform/adapters/lead_identifier.py`
- `sable_platform/adapters/slopper.py`
- `sable_platform/adapters/tracking_sync.py`
- `sable_platform/adapters/base.py` — add `_resolve_repo_path` to `SubprocessAdapterMixin`

**Fix:** Add `_resolve_repo_path(self, env_var: str) -> Path` to `SubprocessAdapterMixin` in
`base.py`. Each adapter's `_repo_path()` becomes a one-liner:
`return self._resolve_repo_path("CULT_GRADER_PATH")`. Keep the env var name in the error
message — it is operator-visible. Saves approximately 28 lines.

**Constraints:** Do not change the exception type (`SableError`) or error code (`INVALID_CONFIG`).
The operator-facing error message must still name the missing env var.

---

### Simplify: Magic constants in merge.py, entities.py, and alert_evaluator.py

**What:** Three threshold values are hardcoded without names:
- `0.70` — merge confidence threshold, appears twice in `merge.py`
- `0.80` — shared-handle merge confidence in `entities.py`
- `14` — tracking staleness days in `alert_evaluator.py`

**Files to touch:**
- `sable_platform/db/merge.py`
- `sable_platform/db/entities.py`
- `sable_platform/workflows/alert_evaluator.py`

**Fix:** Extract to named constants at module top:
- `merge.py`: `MERGE_CONFIDENCE_THRESHOLD = 0.70`
- `entities.py` (or `merge.py` if shared): `SHARED_HANDLE_MERGE_CONFIDENCE = 0.80`
- `alert_evaluator.py`: `TRACKING_STALE_DAYS = 14`

These are separate per-module constants, not a shared config file. Do not create a new
constants module for three values.

---

### Simplify: Inline import json in jobs.py (3 locations)

**What:** `import json` appears inside 3 function bodies in `sable_platform/db/jobs.py` instead
of at module top.

**Files to touch:**
- `sable_platform/db/jobs.py`

**Fix:** Remove the 3 inline `import` statements and add a single `import json` at the top of
the file. No circular import risk.

---

### Simplify: _SEVERITY_RANK dead constant in db/alerts.py

**What:** `_SEVERITY_RANK = {"critical": 3, "warning": 2, "info": 1}` is defined in
`sable_platform/db/alerts.py` but never referenced anywhere in that file or imported by other
files. The live severity ranking logic is in `alert_evaluator.py`.

**Files to touch:**
- `sable_platform/db/alerts.py`

**Fix:** Remove the `_SEVERITY_RANK` constant definition. Grep to confirm zero references before
removing.

---

### Simplify: Deferred import and bare except in cost.py and entities.py

**What:** Two separate issues:

(a) `cost.py` has a `from sable_platform.errors import SableError, BUDGET_EXCEEDED` import
inside a function body with no circular import reason for it.

(b) `add_handle()` in `entities.py` has a `bare except: pass` (or `except Exception: pass`)
block around the `INSERT INTO entity_handles` statement, silently swallowing all DB errors
including schema errors and connection failures.

**Files to touch:**
- `sable_platform/platform/cost.py`
- `sable_platform/db/entities.py` — `add_handle` function

**Fixes:**

(a) Move `from sable_platform.errors import SableError, BUDGET_EXCEEDED` to module top in
`cost.py`. Do NOT move the `import yaml` inside `_read_platform_config()` — that is an
intentional optional-dependency guard and must stay deferred.

(b) Replace the bare/broad `except` in `add_handle()` with
`except sqlite3.IntegrityError: pass` to catch only the expected `UNIQUE` constraint violation.
All other exceptions must propagate so callers can detect real failures. The function continues
after the `INSERT` to update `entity.updated_at` and potentially queue a merge candidate — if
the `INSERT` fails for a non-integrity reason, continuing silently produces corrupt state. This
is a Tier 1 data integrity risk (AGENTS.md: "subprocess adapter result silently ignored when it
signals failure" — same silent-failure principle applies here).

**Gotchas / constraints:**
- Confirm `sqlite3` is already imported at the top of `entities.py` before adding the typed
  except clause.
- The `UNIQUE` constraint violation path (handle already exists) must remain silent — that is
  the intended behavior. Only non-integrity errors should propagate.
