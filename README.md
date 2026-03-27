# SablePlatform

Suite-level backbone for the Sable tool stack.

SablePlatform extracts the shared `sable.platform` layer out of Sable_Slopper and adds a deterministic workflow engine, canonical data contracts, and adapter interfaces to all four Sable repos. Existing repos remain specialized workers. This repo becomes the single owner of cross-suite DB schema, migrations, and coordination logic.

## What this replaces / fixes

| Before | After |
|--------|-------|
| `sable.platform` lives in Sable_Slopper | `sable_platform` is an installable package |
| CultGrader/SableTracking need `SABLE_PROJECT_PATH` | Import directly from `sable_platform` |
| Migration files scattered in Slopper | Owned here, resolved via `importlib.resources` |
| No cross-suite workflow engine | Deterministic `WorkflowRunner` with durable step state |
| No canonical Pydantic contracts | `sable_platform.contracts.*` |
| No observability across the suite | `workflow_runs`, `workflow_steps`, `workflow_events` tables |

## Current scope (v0.3)

- **`sable_platform.db`** — `get_db()`, `ensure_schema()`, all migrations (001–013), entity/tag/merge/jobs/cost/stale helpers
- **`sable_platform.contracts`** — Lead, ProspectHandoff, DiagnosticRun, Entity, ContentItem, Artifact, SyncRun, WorkflowRun, Task, Outcome, Recommendation
- **`sable_platform.workflows`** — deterministic `WorkflowRunner`, `registry`, 5 builtin workflows
- **`sable_platform.adapters`** — subprocess adapters for CultGrader, SableTracking, Slopper, LeadIdentifier
- **`sable_platform.cli`** — `sable-platform workflow`, `sable-platform inspect`, `sable-platform alerts`, `sable-platform actions`, `sable-platform journey`, `sable-platform outcomes`, and `sable-platform org` commands

## Installation

```bash
pip install -e /path/to/SablePlatform
# or in each repo's requirements.txt:
# sable-platform @ file:///path/to/SablePlatform
```

## CLI quickstart

```bash
# Initialize DB (first-time setup)
sable-platform init

# Run a workflow
sable-platform workflow run prospect_diagnostic_sync --org <org_id> --config prospect_yaml_path=/path/to/config.yaml

# Check status
sable-platform workflow status <run_id>

# Resume after CultGrader finishes
sable-platform workflow resume <run_id>

# Cancel a stuck run
sable-platform workflow cancel <run_id>

# List recent runs (machine-readable)
sable-platform workflow list --org <org_id> --json

# Inspect freshness
sable-platform inspect freshness <org_id>

# List active alerts as JSON
sable-platform alerts list --json
```

## Environment variables

| Variable | Purpose |
|----------|---------|
| `SABLE_DB_PATH` | Path to sable.db (default: `~/.sable/sable.db`) |
| `SABLE_TELEGRAM_BOT_TOKEN` | Telegram bot token for alert delivery (optional; delivery skipped if unset) |
| `SABLE_CULT_GRADER_PATH` | Path to Sable_Cult_Grader repo (required for CultGrader adapter) |
| `SABLE_TRACKING_PATH` | Path to SableTracking repo (required for tracking adapter) |
| `SABLE_SLOPPER_PATH` | Path to Sable_Slopper repo (required for Slopper adapter) |
| `SABLE_LEAD_IDENTIFIER_PATH` | Path to Sable_Community_Lead_Identifier repo (required for lead adapter) |

## Repo structure

```
sable_platform/
├── contracts/      Canonical Pydantic models
├── db/             DB layer + migrations
├── workflows/      WorkflowRunner, registry, builtins
├── adapters/       Subprocess adapters per repo
└── cli/            sable-platform CLI
tests/
docs/
```

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Migration Plan](docs/MIGRATION_PLAN.md)
- [Workflows](docs/WORKFLOWS.md)
