# Environment Setup

Complete setup guide for using SablePlatform as the orchestration hub for the Sable tool stack.

---

## Prerequisites

- Python 3.11+
- Virtual environment: `python3 -m venv .venv && source .venv/bin/activate && pip install -e .`
- If you plan to run the PostgreSQL migration or PostgreSQL backups: `pip install -e ".[postgres]"`
- All 4 downstream repos cloned under `~/Projects/` (or wherever — just set the env vars below)

---

## Environment Variables

### SablePlatform (this repo)

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `SABLE_OPERATOR_ID` | **Yes** | — | Your operator identity. Stamped on workflow runs and audit log. CLI exits 1 if unset (only `init` is exempt). |
| `SABLE_HEALTH_TOKEN` | **Yes (health-server)** | — | Bearer token for the `/health` HTTP endpoint. Required before starting `sable-platform health-server`. Generate: `openssl rand -hex 32`. |
| `SABLE_DATABASE_URL` | No | — | SQLAlchemy database URL. When set, runtime commands use it instead of `SABLE_DB_PATH`. |
| `SABLE_DB_PATH` | No | `~/.sable/sable.db` | Path to the shared SQLite database |
| `SABLE_HOME` | No | `~/.sable` | Root dir for config files |
| `SABLE_TELEGRAM_BOT_TOKEN` | No | — | Telegram bot token for alert delivery. If unset, Telegram delivery is silently skipped |

### Adapter Paths (required for workflow execution)

These tell SablePlatform where each downstream repo lives on disk. Workflow steps that call adapters will fail if the corresponding variable is not set.

| Variable | Points To | Used By |
|----------|-----------|---------|
| `SABLE_CULT_GRADER_PATH` | `/path/to/Sable_Cult_Grader` | `CultGraderAdapter` — runs `python diagnose.py` |
| `SABLE_SLOPPER_PATH` | `/path/to/Sable_Slopper` | `SlopperAdvisoryAdapter` — runs `python -m sable advise` |
| `SABLE_TRACKING_PATH` | `/path/to/SableTracking` | `SableTrackingAdapter` — runs `python -m app.platform_sync_runner` |
| `SABLE_LEAD_IDENTIFIER_PATH` | `/path/to/Sable_Community_Lead_Identifier` | `LeadIdentifierAdapter` — runs `python main.py run` |

### Downstream Repo Variables

These are NOT set in SablePlatform — they're set in each downstream repo's own `.env` or shell environment. Adapters inherit the caller's environment, so if you want to run workflows from SablePlatform, you need these in your shell:

**Cult Grader:**
| Variable | Purpose |
|----------|---------|
| `SOCIALDATA_API_KEY` | Twitter data collection (Stage 1) |
| `ANTHROPIC_API_KEY` | All AI stages (3, 5, 6, 7) |
| `DISCORD_BOT_TOKEN` | Discord data collection (per-project) |

**Slopper:**
| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude-powered content generation |

**SableTracking:**
| Variable | Purpose |
|----------|---------|
| `SABLE_CLIENT_ORG_MAP` | JSON mapping `{"ClientName": "org_id"}` |

---

## Shell Configuration

Add to your `~/.zshrc` or `~/.bash_profile`:

```bash
# Operator identity (required — CLI exits 1 without this)
export SABLE_OPERATOR_ID="your_name"

# Health server token (required if running sable-platform health-server)
export SABLE_HEALTH_TOKEN="$(openssl rand -hex 32)"

# Optional: PostgreSQL runtime target
# export SABLE_DATABASE_URL="postgresql://USER:PASS@HOST/DB"

# Sable adapter paths
export SABLE_CULT_GRADER_PATH="$HOME/Projects/Sable_Cult_Grader"
export SABLE_SLOPPER_PATH="$HOME/Projects/Sable_Slopper"
export SABLE_TRACKING_PATH="$HOME/Projects/SableTracking"
export SABLE_LEAD_IDENTIFIER_PATH="$HOME/Projects/Sable_Community_Lead_Identifier"

# API keys (or load from a secrets manager)
export SOCIALDATA_API_KEY="your_key_here"
export ANTHROPIC_API_KEY="your_key_here"
```

---

## Initial Bootstrap

```bash
# 1. Activate venv
cd ~/Projects/SablePlatform
source .venv/bin/activate

# 2. Initialize DB
# Safe to run multiple times — creates ~/.sable/sable.db for SQLite targets
# and runs Alembic when SABLE_DATABASE_URL points at PostgreSQL
sable-platform init

# 3. Verify
sable-platform db-health
sable-platform inspect orgs   # Should return empty table

# 4. Create your first org
sable-platform org create tig --name "TIG Foundation"

# 5. Create your first backup
sable-platform backup

# 6. Verify adapter connectivity
sable-platform workflow preflight --org tig
```

---

## Verification Checklist

Run after setup to confirm everything works:

```bash
# DB is reachable and schema is current
sable-platform db-health

# CLI responds
sable-platform --help

# Adapters are reachable (check output for failures)
sable-platform workflow preflight --org tig

# Test suite passes (optional but recommended)
python3 -m pytest tests/ -q
```

---

## Optional: PostgreSQL Cutover

Use this after you already have a working SQLite-backed install.

```bash
# 1. Install the optional PostgreSQL driver bundle
pip install -e ".[postgres]"

# 2. Migrate the current SQLite dataset into PostgreSQL
sable-platform migrate to-postgres --target-url postgresql://USER:PASS@HOST/DB

# 3. Point future runtime commands at PostgreSQL
export SABLE_DATABASE_URL="postgresql://USER:PASS@HOST/DB"

# 4. `init` / `db-health` / `backup` now target PostgreSQL automatically
sable-platform init
sable-platform db-health
sable-platform backup
```

---

## Directory Structure

```
~/.sable/
├── sable.db          # Shared SQLite database (35 migrations)
├── config.yaml       # Optional: budget cap overrides, platform config
└── profiles/         # Slopper account profiles
    └── @handle/
        ├── tone.md
        ├── interests.md
        ├── context.md
        └── notes.md

~/sable-vault/        # Obsidian-compatible vaults (created by Slopper)
└── {org}/
    ├── notes/
    ├── entities/
    └── ...
```

---

## Troubleshooting

**"Error: SABLE_OPERATOR_ID is not set"** — Add `export SABLE_OPERATOR_ID=your_name` to your shell profile and re-source it. The CLI requires this for all commands except `init`.

**"No module named sable_platform"** — Install in dev mode: `pip install -e .` from the SablePlatform root.

**Adapter check fails in preflight** — Verify `SABLE_*_PATH` env vars point to actual repo directories. The adapter checks that the directory exists and contains the expected entry point file.

**"BUDGET_EXCEEDED" error** — Weekly spend cap hit. Increase via `sable-platform org create` with config or edit `~/.sable/config.yaml`:
```yaml
platform:
  cost_caps:
    max_ai_usd_per_org_per_week: 10.0
```

**Alert delivery not firing** — Check: (1) `SABLE_TELEGRAM_BOT_TOKEN` is set, (2) `sable-platform alerts config show --org ORG` shows a configured `telegram_chat_id` or `discord_webhook`, (3) org is not muted (`sable-platform alerts unmute ORG`), (4) cooldown hasn't suppressed delivery (default 4h).
