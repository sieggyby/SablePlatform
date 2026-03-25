# CLAUDE.md — SablePlatform

## What This Is

SablePlatform is the suite-level backbone for the Sable tool stack. It owns:
- The shared `sable.db` connection factory and all migrations
- Canonical Pydantic contracts for cross-suite data objects
- A deterministic workflow engine (synchronous, durable, resumable)
- Subprocess adapters to each specialized repo
- The `sable-platform` CLI

It does NOT own the business logic of any specialized repo. Those stay in:
- `Sable_Community_Lead_Identifier` — prospect discovery
- `Sable_Cult_Grader` — diagnostic and playbook
- `SableTracking` — intake and contributor tracking
- `Sable_Slopper` — strategy, content, account ops

## Current State

**v0.1** is complete. Includes:
- DB layer (006 migrations, all helpers extracted from Slopper)
- Contracts (all cross-suite Pydantic models)
- WorkflowRunner (synchronous, deterministic, retry/resume/skip_if)
- 2 builtin workflows
- Subprocess adapters for all 4 repos
- CLI (workflow run/resume/status/list/events, inspect orgs/entities/artifacts/freshness)
- 40/40 tests passing

## Architecture Decisions

- **Synchronous workflow execution.** No threading. `poll_diagnostic` step requires manual `sable-platform workflow resume` after CultGrader finishes.
- **Subprocess adapters.** Clean boundary. No cross-repo imports in production paths.
- **DB path.** `SABLE_DB_PATH` env var or `~/.sable/sable.db`. Same file as before.
- **Migration path.** `importlib.resources` — no `SABLE_PROJECT_PATH` needed.
- **Jobs vs workflow tables.** Both coexist. `jobs/job_steps` = Slopper-internal. `workflow_runs/workflow_steps` = cross-suite coordination.

## Working Conventions

- Small patches over rewrites. Don't touch existing repo logic unless explicitly asked.
- Tests use in-memory SQLite — no `~/.sable/sable.db` modification.
- Adapters are subprocess-based; mock them in tests.
- All new workflows go in `sable_platform/workflows/builtins/` and self-register.

## Key Files

| File | Purpose |
|------|---------|
| `sable_platform/db/connection.py` | DB entry point — get_db(), ensure_schema() |
| `sable_platform/db/workflow_store.py` | All workflow table CRUD |
| `sable_platform/workflows/engine.py` | WorkflowRunner — the core state machine |
| `sable_platform/workflows/registry.py` | Register + look up named workflows |
| `sable_platform/cli/workflow_cmds.py` | CLI surface for operators |
| `docs/MIGRATION_PLAN.md` | Step-by-step migration for each existing repo |
