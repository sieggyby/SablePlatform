# QA_WORKFLOW.md

## Goal
Harden Claude Code output without broad rewrites and without letting the repo drift into brittle structure.

## Default workflow

### 1. Review before rewriting
Start by reviewing the current branch, diff, or touched files.
Identify:
- critical risks
- data integrity risks
- maintainability risks
- test gaps
- contract assumptions (especially cross-suite Pydantic contracts)
- hidden coupling between migration, DB helper, and workflow layers

Do not start with a rewrite.

For **new tools built from scratch**, use the **greenfield structural audit** prompt
from `docs/PROMPTS.md` instead of the maintainer review. This is the normal path
for newly generated code, not a special case.

### 2. Add failing tests first
Where practical, add small, high-signal tests for:
- migration idempotency (running `ensure_schema()` twice must not error or duplicate rows)
- workflow resume at each step boundary (step N fails → resume picks up at step N, not N-1 or N+1)
- `skip_if` lambdas triggering correctly based on `ctx.input_data` state
- subprocess adapter returning non-zero / malformed output
- alert dedup: same key blocked when open, allowed after resolve
- tag history integrity: `add_tag()` on replace-current writes 'replaced' before 'added'
- DB helpers leaving no uncommitted state on error paths
- boundary inputs: empty org, zero entities, no sync_runs rows

Use in-memory SQLite via `ensure_schema()`. Tests must not touch `~/.sable/sable.db`.

### 3. Apply the smallest safe patch
Fix only what is necessary to resolve the most important issues.
Prefer:
- local fixes
- small interface corrections
- explicit validation at workflow step boundaries
- bounded retries in `StepDefinition.max_retries`
- targeted refactors in touched code

Avoid:
- broad rewrites
- speculative abstractions
- moving large sections of code without clear need
- changing migration SQL that has already been applied to production databases

### 4. Clean up touched files
After tests pass, reduce duplication and improve readability in touched files only.
Prefer deletion over addition when safe.
Remove dead code. Verify no DB path or sensitive config appears in added print or log statements.

### 5. Validate
Run the most relevant repo commands after edits:
```bash
python3 -m pytest tests/ -x -q
```

Additionally verify:
- `SABLE_DB_PATH` does not appear in committed test fixtures
- schema version in the latest migration matches `_MIGRATIONS` list in `connection.py`
- new builtin workflows are registered in `registry.py`
- no subprocess adapter calls are made in test paths (mock adapters)
- estimated per-run cost: alert evaluator and workflow engine have no direct API spend

## Constraints
- Preserve current contracts unless explicitly authorized to change them.
- Do not change migration SQL that has already been applied — add a new migration instead.
- Do not introduce new Python dependencies casually.
- Keep diffs scoped and reviewable.
- Do not refactor unaffected modules unless directly blocking a safe fix.
- If a larger refactor is truly needed, explain why before doing it.

## Definition of a good Codex pass
A good pass should:
- reduce structural or data integrity risk
- add confidence through tests
- keep the diff understandable
- avoid unnecessary novelty
- not introduce schema, contract, or reliability regressions
