# PROMPTS.md

Use these prompts by frequency. Do not run everything by default.

---

## Default prompts
Use these in normal day-to-day review loops.
For new tools with no prior branch, use the **greenfield structural audit** in place of the maintainer review.

### 1) Instruction sanity check
```text
Read `AGENTS.md`, `docs/QA_WORKFLOW.md`, `docs/PROMPTS.md`, and `docs/THREAT_MODEL.md`.
Summarize the instructions you will follow in this repo before doing any work.
Keep it brief. Include: review priorities, repo context, and security baseline.
```

### 2) Maintainer review
```text
Review the current branch as the maintainer responsible for reliability and future change velocity.
Use `docs/THREAT_MODEL.md` to inform your risk assessment for this repo.

Focus on:
- hidden coupling between migration, DB helper, workflow engine, and CLI layers
- schema drift between SQL migration and Python DB helpers
- workflow resume correctness (step boundaries, skip_if, retry)
- alert dedup correctness (open vs resolved status, dedup_key uniqueness)
- cross-suite contract drift (Pydantic models consumed by other repos)
- test gaps on critical paths
- subprocess adapter failures silently ignored

Do not rewrite yet.

Output only:
1. critical risks
2. data integrity risks
3. maintainability risks
4. minimal corrective plan
5. exact tests to add

If no issues exist at a given level, say so and move on.
```

### 3) Greenfield structural audit
> Use instead of maintainer review when Claude Code has built a new tool from scratch.

```text
This is a new tool or an early codebase with no stable baseline yet.
Review it for structural risks before feature accretion makes them expensive.
Use `docs/THREAT_MODEL.md` to inform your risk assessment.

Focus on:
- migration ordering and idempotency
- workflow step boundaries and partial failure recovery
- subprocess adapter result handling (non-zero exit, malformed output)
- alert dedup logic correctness
- DB helper commit discipline (missing conn.commit() on error paths)
- cross-suite Pydantic contract assumptions

Output only:
1. critical structural risks
2. data integrity risks
3. maintainability risks
4. minimal corrective plan
5. exact tests to add now

If no issues exist at a given level, say so and move on.
```

### 4) Add failing tests first
```text
Based on the current branch, add failing tests for the most important edge cases
and contract assumptions introduced by these changes.

Prioritize tests for:
- migration idempotency (ensure_schema() twice must not error or duplicate)
- workflow resume at each step boundary
- skip_if lambdas with missing or unexpected ctx.input_data keys
- subprocess adapter returning failure or malformed output
- alert dedup: blocked when open, allowed after resolve, no key = never blocked
- DB helpers: no uncommitted state left on error paths
- boundary inputs: empty org, no rows, single row

Use in-memory SQLite via ensure_schema(). Mock subprocess adapters.
Do not make real filesystem writes to ~/.sable/.
Prefer small, high-signal tests. Each test should have a clear name describing what breaks.
```

### 5) Smallest safe patch
```text
Implement the smallest safe patch set needed to make those tests pass.
Avoid broad rewrites. Preserve existing contracts unless a critical flaw requires a minimal structural fix.

If a fix touches DB helpers, verify:
- conn.commit() is called correctly on all success paths
- error paths do not leave partial rows
- migration SQL has not been modified retroactively

If a fix touches workflow engine, verify:
- step output is still propagated into ctx.input_data for subsequent steps
- skip_if still evaluates lazily using the live ctx at step entry
- retry count is decremented correctly on each attempt
```

### 6) Cleanup pass
```text
Reduce duplication and improve clarity in touched files only.
Prefer deletion over addition. Do not change behavior.
Remove dead code. Verify no DB path or env var appears in added log statements.
```

---

## Periodic prompts
Use weekly or after major changes, not on every branch.

### 7) Migration integrity audit
```text
Audit the migration registry and SQL files for integrity risks.

Focus on:
- _MIGRATIONS list in connection.py matches actual SQL files on disk
- schema_version UPDATE at the end of each migration file matches its list entry
- IF NOT EXISTS guards on all CREATE TABLE and CREATE INDEX statements
- no migration modifies a previously applied table's column types
- new migrations do not assume column ordering that could change

List findings first. Then propose the smallest corrective plan.
```

### 8) Workflow correctness audit
```text
Audit the workflow engine and all builtin workflows for correctness risks.

Focus on:
- skip_if lambdas that could silently skip due to missing keys (None vs False)
- steps that do not propagate output into ctx.input_data for downstream steps
- subprocess adapter steps with no error handling on non-zero exit
- resume logic: does the engine correctly identify the first non-completed step?
- retry logic: is the error cleared between attempts?

List findings first. Then propose the smallest corrective plan.
```

### 9) Alert evaluator audit
```text
Audit the alert evaluator and dedup logic for correctness risks.

Focus on:
- dedup_key uniqueness collisions (two different conditions generating the same key)
- status filter in dedup check: only 'new' should block re-alert
- staleness thresholds hardcoded vs configurable
- evaluator SQL queries selecting wrong org or wrong status
- delivery function: does it log to stderr correctly and not to stdout?

List findings first. Then propose the smallest corrective plan.
```

---

## Situational prompts
Use only when relevant.

### 10) Cross-suite contract audit
```text
Audit the Pydantic contracts in sable_platform/contracts/ for cross-suite drift.

Focus on:
- fields consumed by Sable_Cult_Grader, SableTracking, or Slopper that have changed type or been removed
- contracts that do not match their corresponding SQL schema column types
- Optional fields that downstream repos assume are always present
- INTEGER vs TEXT PK mismatches between SQL schema and Python models

List findings first. Then propose the smallest safe fix.
```

### 11) Subprocess adapter audit
```text
Audit all subprocess adapters for resilience.

Focus on:
- non-zero exit codes silently treated as success
- stdout/stderr parsing fragile to whitespace or encoding changes
- no timeout set on subprocess call (can hang indefinitely)
- adapter result not validated before downstream workflow step uses it

List findings first. Then propose the smallest safe fix.
```
