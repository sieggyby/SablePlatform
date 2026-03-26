# THREAT_MODEL.md

## Purpose
This document gives Codex an adversarial lens tuned to how SablePlatform actually fails.
Use it to prioritize what to test and what to harden.

> Edit the Repo-specific threats section when new features or failure modes are discovered.

---

## Common threat categories for Sable tools

### 1. External API failure
What happens: an upstream API errors, times out, rate-limits, or changes shape.

What to test:
- subprocess adapter returning non-zero exit code
- subprocess adapter stdout empty or malformed
- subprocess adapter hanging without a timeout

What to check:
- adapter result is validated before downstream steps consume it
- non-zero exit is surfaced as a step error, not silently swallowed
- subprocess calls have timeout limits

### 2. Cost overrun
What happens: a subprocess adapter call or workflow step triggers unbounded spend in a downstream repo.

What to test:
- workflow with `max_retries > 0` on an adapter step that always fails
- alert evaluator called in a tight loop

What to check:
- `max_retries` is set conservatively on adapter steps
- alert evaluator makes no external API calls (DB queries only)
- no adapter step is called inside an unbounded loop

### 3. Credential exposure
What happens: `SABLE_DB_PATH` or downstream API keys inherited by subprocess adapters appear in logs or CLI output.

What to test:
- failing adapter calls with verbose output
- CLI error messages on DB connection failure

What to check:
- no env vars appear in committed test fixtures or hardcoded strings
- tracebacks and request logging do not surface calling-environment credentials
- `SABLE_DB_PATH` is loaded from env, never committed

### 4. Pipeline partial failure
What happens: a workflow step writes state (DB rows, artifacts) and a later step fails, leaving stale or inconsistent intermediate state.

What to test:
- force failure at each step in the workflow sequence
- resume after mid-workflow failure — engine must restart at the failed step, not the beginning
- `skip_if` evaluating against stale `ctx.input_data` from a prior partial run

What to check:
- workflow resume correctly identifies the first non-completed step
- steps that write DB rows are idempotent or handle duplicate key gracefully
- failed runs do not leave orphaned `workflow_steps` rows in `running` status

### 5. Schema drift
What happens: a Python DB helper assumes a column exists that has not been added via migration, or a Pydantic contract field is renamed without updating SQL consumers.

What to test:
- running helpers against a database at the previous schema version
- inserting Pydantic model data into a table where a field was recently renamed

What to check:
- every new column has a corresponding migration
- migration list in `connection.py` is complete and in order
- Pydantic contracts match their SQL counterparts for column names and types

### 6. Empty and degenerate inputs
What happens: an org has no entities, no sync runs, no diagnostic runs, or no workflow history.

What to test:
- alert evaluator with no rows in any table
- `entity_funnel()` with zero entities
- `compute_and_store_diagnostic_delta()` with only one run

What to check:
- divide-by-zero cases are handled (action_summary execution_rate with zero completed)
- empty results return `[]` or `{}` rather than raising
- CLI commands handle zero-row output gracefully

### 7. Output trustworthiness failure
What happens: the platform produces alert or diagnostic output that looks authoritative but is based on stale, missing, or inconsistent upstream data.

What to test:
- alert evaluator fires on a stale `tracking_stale` condition after the underlying data has been refreshed
- `dedup_key` uniqueness: two different conditions generating the same key silently block each other

What to check:
- dedup key namespacing is collision-resistant
- resolved alerts do not block re-alerting on the same condition
- diagnostic deltas reference the correct `run_id_before` (latest prior completed run, not just any run)

---

## Repo-specific threats

### 1. Migration applied out of order or with wrong version
What happens: `_MIGRATIONS` list in `connection.py` lists migrations in the wrong order, or a migration's `UPDATE schema_version SET version = N` does not match its position in the list. Downstream helpers crash or silently query missing columns.

What to test:
- `ensure_schema()` on a fresh in-memory DB → assert final version equals expected max
- `ensure_schema()` called twice → no duplicate rows, no errors
- manually set schema_version to N-1 → confirm migration N is applied on next call

What to check:
- every SQL file ends with `UPDATE schema_version SET version = N` where N matches its list entry
- `IF NOT EXISTS` guards on all DDL statements
- no migration retroactively alters column types of previously created tables

### 2. Workflow resume picking up the wrong step
What happens: `WorkflowRunner.resume()` identifies the restart point incorrectly — either re-running already-completed steps (data duplication) or skipping the failed step (data gap).

What to test:
- inject a failure at step N in an N+3 step workflow; resume; confirm only steps N onward execute
- resume on a workflow where step N has `status='running'` (crashed mid-execution)
- resume on a fully completed workflow → confirm no steps re-run

What to check:
- resume logic selects the first step where `status != 'completed'`
- steps in `running` state at resume time are treated as failed (not skipped)
- already-completed step outputs are still available in `ctx.input_data` after resume

### 3. Alert dedup collision or bypass
What happens: two different alert conditions produce the same `dedup_key`, causing one to silently block the other. Or the dedup check uses the wrong status filter and blocks re-alerting after resolve.

What to test:
- create an alert with `dedup_key='tracking_stale:org1'`; attempt to create `workflow_failed:org1` with a different key → confirm not blocked
- resolve an alert; create a new one with the same dedup_key → confirm allowed
- same dedup_key, status='acknowledged' (not 'resolved') → confirm still blocked

What to check:
- `dedup_key` format is `{alert_type}:{discriminator}` — no two alert types share a namespace
- dedup query filters `status='new'` only — acknowledged alerts still block, resolved do not
- `create_alert()` returns `None` (not raises) when blocked by dedup

### 4. Tag history / entity_tag state divergence
What happens: `_record_tag_history()` fails silently (pre-migration-008 try/except) but `entity_tags` is still mutated — or vice versa — leaving the history log inconsistent with current tag state.

What to test:
- `add_tag()` on a database without `entity_tag_history` table → tag is written, no exception raised
- `add_tag()` replacing an existing tag → exactly one 'replaced' + one 'added' history row, no more
- `expire_tags()` → one 'expired' history row per expired tag

What to check:
- try/except in `_record_tag_history()` logs the failure without suppressing the tag write
- no history row is written for tags that were not actually mutated
- `entity_tag_history` rows reference valid `entity_id` values (no orphaned history)

### 5. Subprocess adapter hanging or producing silent garbage
What happens: an adapter to Cult Grader, SableTracking, or Slopper hangs indefinitely (no timeout), or returns exit code 0 with malformed stdout that the workflow step silently accepts.

What to test:
- mock adapter that returns exit code 0 with empty stdout → step should surface an error, not propagate empty result
- mock adapter that sleeps past the timeout → confirm workflow step fails with a clear error

What to check:
- all `subprocess.run()` calls have `timeout` set
- adapter stdout is validated (non-empty JSON or expected schema) before the step marks itself completed
- `StepResult.status='failed'` is returned (not raised) when adapter output is unusable
