# SablePlatform

Suite-level backbone for the Sable tool stack.

SablePlatform owns the shared `sable.db` layer, canonical Pydantic contracts, a deterministic workflow engine, subprocess adapters to all four Sable repos, and the `sable-platform` CLI.

## Current scope (v0.5)

- **`sable_platform.db`** — `get_db()`, `ensure_schema()`, 30 migrations, entity/tag/merge/jobs/cost/stale/alerts/interactions/decay/centrality/prospects/playbook/watchlist/audit/webhooks helpers
- **`sable_platform.contracts`** — Lead, ProspectHandoff, DiagnosticRun, Entity, ContentItem, Artifact, SyncRun, WorkflowRun, Task, Outcome, Recommendation, TrackingMetadata
- **`sable_platform.workflows`** — deterministic `WorkflowRunner`, registry, 5 builtin workflows, 12 alert checks, alert delivery (Telegram/Discord with cooldown)
- **`sable_platform.adapters`** — subprocess adapters for CultGrader, SableTracking, Slopper, LeadIdentifier
- **`sable_platform.cli`** — full operator surface (see [CLI Reference](docs/CLI_REFERENCE.md))

## Installation

```bash
pip install -e /path/to/SablePlatform
# or, if you plan to run the PostgreSQL migration / backup path:
pip install -e "/path/to/SablePlatform[postgres]"
```

## CLI quickstart

```bash
# Initialize DB
export SABLE_OPERATOR_ID=your_name   # required before all commands except init
sable-platform init
sable-platform db-health             # backend-neutral DB healthcheck for Docker/automation

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

# Platform health + metrics
sable-platform inspect health tig
sable-platform health-server        # serves GET /health on :8765 (requires SABLE_HEALTH_TOKEN)
sable-platform metrics              # Prometheus text format to stdout
```

## Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `SABLE_OPERATOR_ID` | **Yes** | Operator identity stamped on workflow runs and audit log. CLI exits 1 if unset (except `init`). |
| `SABLE_HEALTH_TOKEN` | **Yes (health-server)** | Bearer token for `/health` endpoint. Generate: `openssl rand -hex 32`. |
| `SABLE_DATABASE_URL` | No | SQLAlchemy database URL. When set, runtime connections use it instead of `SABLE_DB_PATH`. |
| `SABLE_DB_PATH` | No | Path to sable.db (default: `~/.sable/sable.db`) |
| `SABLE_HOME` | No | Root dir for config (default: `~/.sable`) |
| `SABLE_TELEGRAM_BOT_TOKEN` | No | Telegram bot token for alert delivery (optional) |
| `SABLE_CULT_GRADER_PATH` | No | Path to Sable_Cult_Grader repo |
| `SABLE_SLOPPER_PATH` | No | Path to Sable_Slopper repo |
| `SABLE_TRACKING_PATH` | No | Path to SableTracking repo |
| `SABLE_LEAD_IDENTIFIER_PATH` | No | Path to Sable_Community_Lead_Identifier repo |

## Postgres Migration Path

SQLite remains the default bootstrap path. To move an existing `sable.db` into PostgreSQL:

```bash
# 1. Install the optional driver bundle
pip install -e "/path/to/SablePlatform[postgres]"

# 2. Migrate the local SQLite data set into Postgres
sable-platform migrate to-postgres --target-url postgresql://USER:PASS@HOST/DB

# 3. Point the runtime at Postgres
export SABLE_DATABASE_URL=postgresql://USER:PASS@HOST/DB
```

When `SABLE_DATABASE_URL` points at PostgreSQL, `sable-platform init` applies Alembic migrations to that database, normal runtime commands use it, `sable-platform db-health` checks it without needing an org ID, and `sable-platform backup` switches to `pg_dump` automatically.

## Repo structure

```
sable_platform/
├── contracts/      Canonical Pydantic models
├── db/             DB layer + 30 migrations
├── workflows/      WorkflowRunner, registry, builtins, alert engine
├── adapters/       Subprocess adapters per repo
├── webhooks/       HMAC-SHA256 webhook dispatch
├── http_health.py  Bearer-authenticated /health HTTP server
├── metrics.py      Prometheus text format export
├── cron.py         Crontab scheduler
└── cli/            sable-platform CLI
tests/              996 tests (in-memory SQLite, no ~/.sable modification)
docs/
```

## Docs

- [Architecture](docs/ARCHITECTURE.md) — module map, DB ownership, engine design
- [CLI Reference](docs/CLI_REFERENCE.md) — complete command reference
- [Cross-Repo Integration](docs/CROSS_REPO_INTEGRATION.md) — adapter reference, data flows, direct commands
- [End-to-End Workflows](docs/END_TO_END_WORKFLOWS.md) — operational runbooks
- [Environment Setup](docs/ENVIRONMENT_SETUP.md) — full setup guide
- [Workflows](docs/WORKFLOWS.md) — builtin workflow definitions and state machines
- [Schema Contracts](docs/SCHEMA_CONTRACTS.md) — cross-suite data contracts
- [Alert System](docs/ALERT_SYSTEM.md) — alert lifecycle, all 12 checks, delivery channels, dedup/cooldown reference
- [Extending](docs/EXTENDING.md) — how to add a workflow, adapter, alert check, or migration
- [SocialData Best Practices](docs/SOCIALDATA_BEST_PRACTICES.md) — cross-tool API reference
