# SablePlatform

Suite-level backbone for the Sable tool stack.

SablePlatform owns the shared `sable.db` layer, canonical Pydantic contracts, a deterministic workflow engine, subprocess adapters to all four Sable repos, and the `sable-platform` CLI.

## Current scope (v0.4)

- **`sable_platform.db`** — `get_db()`, `ensure_schema()`, 23 migrations, entity/tag/merge/jobs/cost/stale/alerts/interactions/decay/centrality/prospects/playbook/watchlist/audit/webhooks helpers
- **`sable_platform.contracts`** — Lead, ProspectHandoff, DiagnosticRun, Entity, ContentItem, Artifact, SyncRun, WorkflowRun, Task, Outcome, Recommendation
- **`sable_platform.workflows`** — deterministic `WorkflowRunner`, registry, 5 builtin workflows, 12 alert checks, alert delivery (Telegram/Discord with cooldown)
- **`sable_platform.adapters`** — subprocess adapters for CultGrader, SableTracking, Slopper, LeadIdentifier
- **`sable_platform.cli`** — full operator surface (see [CLI Reference](docs/CLI_REFERENCE.md))

## Installation

```bash
pip install -e /path/to/SablePlatform
```

## CLI quickstart

```bash
# Initialize DB
sable-platform init

# Run a workflow
sable-platform workflow run weekly_client_loop --org tig

# Operator dashboard
sable-platform dashboard

# Health check
sable-platform inspect health tig

# Alerts
sable-platform alerts list --severity critical --status new
sable-platform alerts evaluate --org tig

# Inspect community graph
sable-platform inspect decay tig --tier critical
sable-platform inspect centrality tig --json
sable-platform inspect interactions tig --type reply --min-count 3

# Playbook targets/outcomes
sable-platform inspect playbook tig
sable-platform inspect playbook tig --outcomes --json
```

## Environment variables

| Variable | Purpose |
|----------|---------|
| `SABLE_DB_PATH` | Path to sable.db (default: `~/.sable/sable.db`) |
| `SABLE_HOME` | Root dir for config (default: `~/.sable`) |
| `SABLE_TELEGRAM_BOT_TOKEN` | Telegram bot token for alert delivery (optional) |
| `SABLE_CULT_GRADER_PATH` | Path to Sable_Cult_Grader repo |
| `SABLE_SLOPPER_PATH` | Path to Sable_Slopper repo |
| `SABLE_TRACKING_PATH` | Path to SableTracking repo |
| `SABLE_LEAD_IDENTIFIER_PATH` | Path to Sable_Community_Lead_Identifier repo |

## Repo structure

```
sable_platform/
├── contracts/      Canonical Pydantic models
├── db/             DB layer + 23 migrations
├── workflows/      WorkflowRunner, registry, builtins, alert engine
├── adapters/       Subprocess adapters per repo
├── webhooks/       HMAC-SHA256 webhook dispatch
├── cron.py         Crontab scheduler
└── cli/            sable-platform CLI
tests/              764 tests (in-memory SQLite, no ~/.sable modification)
docs/
```

## Docs

- [Architecture](docs/ARCHITECTURE.md) — module map, DB ownership, engine design
- [CLI Reference](docs/CLI_REFERENCE.md) — complete command reference
- [Cross-Repo Integration](docs/CROSS_REPO_INTEGRATION.md) — adapter reference, data flows, direct commands
- [End-to-End Workflows](docs/END_TO_END_WORKFLOWS.md) — operational runbooks
- [Environment Setup](docs/ENVIRONMENT_SETUP.md) — full setup guide
- [Workflows](docs/WORKFLOWS.md) — builtin workflow definitions and state machines
- [SocialData Best Practices](docs/SOCIALDATA_BEST_PRACTICES.md) — cross-tool API reference
