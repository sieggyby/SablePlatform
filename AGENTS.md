# AGENTS.md

## Purpose
Use Codex in this repo primarily as a skeptical maintainer and QA layer for Claude Code output.

## Default stance
Assume the code may work while still hiding structural problems.
Prioritize finding production, data integrity, and maintainability risks before proposing broad implementation changes.

When you find issues: flag the risk, then fix what you can in a single scoped patch.
If a safe fix is too large, leave a `TODO(codex)` with a one-line explanation.
If no issues are found at a given priority tier, say so and move on. Do not invent findings.

---

## Repo domain context

This repo is the backbone for the Sable tool stack. It owns `sable.db`, all DB migrations, canonical Pydantic contracts, the workflow engine, subprocess adapters, and the `sable-platform` CLI.

| Trait | Repo-specific assumption |
|---|---|
| External dependencies | No direct external API calls. Subprocess adapters shell out to Cult Grader, SableTracking, and Slopper — each of which may call Anthropic, SocialData, or Replicate |
| Architecture | Synchronous workflow engine with deterministic step execution, retry, skip_if, and resume. State persisted in SQLite (`sable.db`) or PostgreSQL (via `SABLE_DATABASE_URL`). SQLite migrations are append-only SQL files; Postgres uses Alembic. Dual-migration required for schema changes |
| Reliability risk | Workflow partial failure leaves steps in `running` or `pending` state; resume must correctly identify the restart point. Migration version drift causes schema mismatches across the suite |
| Auth surface | HTTP `/health` endpoint requires `SABLE_HEALTH_TOKEN` Bearer token. CLI requires `SABLE_OPERATOR_ID` (exits 1 if unset, except `init`). No API keys owned by this repo. Subprocess adapters inherit env from caller. |
| Output formats | SQLite rows, CLI table output (fixed-width), Pydantic JSON contracts passed between suite repos |
| Deployment | Local CLI + in-process library + Docker compose. SQLite at `~/.sable/sable.db` or `$SABLE_DB_PATH` for local dev; PostgreSQL via `$SABLE_DATABASE_URL` for production (live on Hetzner VPS). Container runs `health-server` + `alerts evaluate` loop |
| Cost sensitivity | No direct API cost. Subprocess adapter calls may trigger spend in downstream repos — flag unbounded adapter call loops |

### Repo-specific cost targets
- No direct AI spend. Subprocess adapter calls are fire-and-forget — downstream repos own their cost guardrails.
- Alert evaluator runs are synchronous DB queries only: no API cost.

Use this context to sharpen prioritization. Prefer repo-specific risk calls over generic advice.

---

## Core rules
- Prefer small, reviewable patches over rewrites.
- Do not add dependencies unless clearly justified.
- Do not silently change API, schema, or persistence contracts.
- For bug fixes, reproduce with a failing test first when practical.
- Preserve current behavior unless the task explicitly requests behavior change.
- Prefer deletion over addition when the same outcome can be achieved safely.
- Avoid refactoring untouched modules unless directly required for a safe fix.
- Run the most relevant validation commands after making changes.

---

## Review priorities

### Tier 1 — breaks prod, corrupts data, or breaks cross-suite contracts
- migration applied out of order or with incorrect version bump
- schema change without migration (column added/removed in code but not SQL) — dual-migration: must update both SQL migration file + `_MIGRATIONS` entry (SQLite) AND Alembic revision (Postgres)
- workflow resume picking up the wrong step or re-running completed steps
- subprocess adapter result silently ignored when it signals failure
- `dedup_key` logic failure causing alert double-fire or silent drop
- tag history writes failing without surfacing — tag state diverges from history

### Tier 2 — breaks maintainers
- Pydantic contract change not reflected in all suite consumers
- DB helper bypassing `conn.commit()` or leaving uncommitted state
- `ensure_schema()` applying migrations out of order or idempotency failure
- `skip_if` lambdas capturing mutable ctx state incorrectly
- `WorkflowRunner` not propagating step output into subsequent `ctx.input_data`
- Alert evaluator SQL queries selecting wrong status filter

### Tier 3 — slows future work
- test gaps on the workflow engine (retry, skip, resume paths)
- missing edge-case handling in alert dedup or evaluator checks
- accidental complexity in migration registry
- misleading naming or comments in DB helpers

---

## Standard review output
When asked to review a branch, PR, diff, or new codebase, output in this order:

1. Critical risks
2. Data integrity risks
3. Maintainability risks
4. Minimal corrective plan
5. Exact tests to add

If no issues exist at a given level, state that and move on.

---

## Security baseline
- No API keys are owned by this repo.
- `SABLE_HEALTH_TOKEN` is a bearer secret — must not be logged, committed, or surfaced in error output. `SABLE_TELEGRAM_BOT_TOKEN` is a bot credential with the same constraint.
- `SABLE_DB_PATH` points to a local file (not a credential).
- DB path must not appear in committed test fixtures or hardcoded strings.
- Subprocess adapter calls must not log or surface credentials from the calling environment.
- Generated files (reports, artifacts) must not interpolate env vars.

---

## Cost guardrails
- Flag any subprocess adapter call inside an unbounded loop.
- Alert evaluator must not make external API calls — it is DB-query-only.
- Workflow steps that shell out to adapter repos must have bounded retry counts (`max_retries` on `StepDefinition`).
- `compute_and_store_diagnostic_delta()` reads from disk — flag any pattern that reads `computed_metrics.json` more than once per pair of runs.

---

## Repo-specific context
- `sable_platform/db/connection.py` owns the migration list (`_MIGRATIONS`) — currently 31 migrations (001–031). If a new SQL file is added without a corresponding entry here, it will never be applied. Each migration SQL must include its own `UPDATE schema_version` statement (DDL auto-commits in Python sqlite3, breaking context manager transactions).
- `sable_platform/workflows/registry.py` auto-registers builtins via import side effects. A new builtin workflow that is not imported in `_auto_register()` will never appear in `sable-platform workflow list`.
- `entity_tag_history` writes in `sable_platform/db/tags.py` are wrapped in try/except to handle pre-migration-008 databases. This is intentional — do not remove the guard.
- `diagnostic_runs.run_id` is INTEGER (auto-increment), not TEXT. `diagnostic_deltas` uses plain `INTEGER NOT NULL` for `run_id_before/run_id_after` (no FK constraint) to avoid type mismatch.
- All tests use in-memory SQLite via `ensure_schema()` — no `~/.sable/sable.db` modification.

---

## Additional guidance
See `docs/QA_WORKFLOW.md` for the default hardening workflow.
See `docs/PROMPTS.md` for default, periodic, and situational prompts.
See `docs/THREAT_MODEL.md` for the adversarial testing lens.
