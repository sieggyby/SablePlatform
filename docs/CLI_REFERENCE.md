# CLI Reference — `sable-platform`

Complete command reference for the SablePlatform CLI. All commands operate on `sable.db` at `~/.sable/sable.db` (override with `SABLE_DB_PATH`).

---

## Global Options

| Flag | Description |
|------|-------------|
| `--verbose, -v` | Enable debug logging |

## Bootstrap & Maintenance

```bash
sable-platform init                      # Create sable.db + apply all 30 migrations
sable-platform init --db-path /alt/path  # Use non-default DB location
```

## backup — Database Backup

Uses SQLite's online backup API — safe for live WAL-mode databases.

```bash
sable-platform backup                                    # Backup to ~/.sable/backups/
sable-platform backup --dest /mnt/backups                # Custom destination
sable-platform backup --label pre_migration              # Label in filename (alphanumeric, _, -)
sable-platform backup --max-backups 5                    # Keep only 5 most recent (default: 10, 0=unlimited)
sable-platform backup --db-path /alt/sable.db            # Backup a non-default DB
```

---

## cron — Scheduled Workflow Runs

Manages crontab entries for automated workflow execution. All inputs are validated against shell injection.

```bash
# Add a scheduled workflow
sable-platform cron add --org tig --workflow weekly_client_loop --schedule weekly-thursday
sable-platform cron add --org tig --workflow alert_check --schedule "0 22 * * 4"
sable-platform cron add --org tig --workflow weekly_client_loop --schedule twice-weekly
sable-platform cron add --org tig --workflow prospect_diagnostic_sync --schedule daily \
  --extra-args "-c prospect_yaml_path=/path/to/tig.yaml"

# List all sable-platform cron entries
sable-platform cron list

# Remove a scheduled workflow
sable-platform cron remove --org tig --workflow weekly_client_loop

# Show available schedule presets
sable-platform cron presets
```

**Presets:** `hourly`, `daily`, `twice-weekly`, `weekly-monday` through `weekly-sunday`. Or pass any 5-field cron expression directly.

**Security:** `org` and `workflow` must be `[A-Za-z0-9_-]` only. All command values are shell-quoted via `shlex.quote()`. Newlines, colons, and shell metacharacters are rejected.

---

## org — Manage Orgs

```bash
sable-platform org create <ORG_ID> --name "Display Name"   # Create org
sable-platform org create <ORG_ID> --name "Name" --status inactive
sable-platform org list                                      # List all orgs
sable-platform org list --json                               # JSON output
sable-platform org graduate <PROJECT_ID>                     # Mark prospect as graduated (converted to client)
sable-platform org reject <PROJECT_ID> [--reason "bad fit"]  # Mark prospect as rejected
```

### org config — Per-Org Configuration

Reads and writes `config_json` on the `orgs` row. No migration needed — the column already exists.

```bash
sable-platform org config set tig sector DeFi        # Set sector (validated enum)
sable-platform org config set tig stage growth        # Set stage (validated enum)
sable-platform org config set tig max_ai_usd_per_org_per_week 10.0  # Override cost cap
sable-platform org config set tig decay_warning_threshold 0.6       # Override alert threshold

sable-platform org config get tig                    # Show all config for org
sable-platform org config get tig sector             # Show one key
sable-platform org config get tig --json             # JSON output

sable-platform org config list                       # Config for all orgs
sable-platform org config list --json
```

**Valid sectors:** `DeFi`, `Gaming`, `Infrastructure`, `L1/L2`, `Social`, `DAO`, `NFT`, `AI`, `Other`

**Valid stages:** `pre_launch`, `launch`, `growth`, `mature`, `declining`

Numeric threshold keys are coerced to `float` and validated against min/max bounds. Out-of-range values are rejected. See `docs/ALERT_SYSTEM.md` § Per-Org Threshold Overrides for the full list of supported threshold keys and their ranges.

---

## schema — JSON Schema Export

```bash
sable-platform schema                          # Export JSON Schema for all 8 Pydantic contracts to docs/schemas/
sable-platform schema --stdout                 # Print JSON Schema to stdout instead of writing files
sable-platform schema --output-dir ./schemas   # Write individual .json files to a custom directory
```

---

## gc — Data Retention

```bash
sable-platform gc --retention-days 90          # Delete records older than 90 days (FK-safe, audit log immune)
```

---

## workflow — Workflow Execution

### Run, Resume, Cancel

```bash
# Start a workflow
sable-platform workflow run <WORKFLOW_NAME> --org <ORG_ID> [-c key=value ...]

# Examples
sable-platform workflow run onboard_client --org psy_protocol \
  -c prospect_yaml_path=/path/to/psy_protocol.yaml
sable-platform workflow run prospect_diagnostic_sync --org tig \
  -c prospect_yaml_path=/path/to/tigfoundation.yaml
sable-platform workflow run weekly_client_loop --org tig
sable-platform workflow run alert_check --org tig
sable-platform workflow run lead_discovery --org tig

# Resume a failed run
sable-platform workflow resume <RUN_ID>
sable-platform workflow resume <RUN_ID> --ignore-version-check  # Emergency: skip fingerprint check

# Cancel a run
sable-platform workflow cancel <RUN_ID>
```

### Monitor

```bash
sable-platform workflow status <RUN_ID>          # Single run status
sable-platform workflow status <RUN_ID> --json
sable-platform workflow list --org <ORG_ID>      # Recent runs
sable-platform workflow list --org tig --workflow weekly_client_loop --limit 5
sable-platform workflow list --org tig --json
sable-platform workflow events <RUN_ID>          # Step-by-step event log
```

### Maintenance

```bash
sable-platform workflow gc                # Mark stuck runs (>6h) as timed_out
sable-platform workflow gc --hours 12     # Custom threshold
sable-platform workflow unlock <RUN_ID>  # Release execution lock (for stuck-lock recovery)
sable-platform workflow preflight --org tig   # Health gate: exit 0=ready, exit 1=problems
sable-platform workflow preflight             # Check all active orgs
```

### Built-in Workflows

| Workflow | Purpose | Required Config |
|----------|---------|-----------------|
| `onboard_client` | Readiness check: verify org, adapters, create initial sync record | — |
| `prospect_diagnostic_sync` | Diagnose, sync entities, register artifacts | `prospect_yaml_path` |
| `weekly_client_loop` | Recurring: freshness, refresh, strategy, alerts | — |
| `alert_check` | Evaluate all alert conditions | — |
| `lead_discovery` | Run Lead Identifier, sync scores, register artifacts | — |

---

## inspect — Read-Only Queries

### Org-Level

```bash
sable-platform inspect orgs                             # List all orgs
sable-platform inspect entities <ORG_ID> [--limit 50]   # List entities
sable-platform inspect artifacts <ORG_ID> [--limit 20]  # List artifacts
sable-platform inspect freshness <ORG_ID>               # Data age: tracking, diag, strategy
sable-platform inspect health <ORG_ID> [--json]         # Unified health: syncs, alerts, discord, workflows
```

### Community Graph

```bash
# Interaction edges (relationship web)
sable-platform inspect interactions <ORG_ID> [--type reply|mention|co_mention] [--min-count 3] [--limit 50] [--json]

# Decay scores (churn risk) — default --min-score: 0.5
sable-platform inspect decay <ORG_ID> [--min-score 0.5] [--tier critical|high|medium|low] [--limit 50] [--json]

# Centrality scores (bridge nodes)
sable-platform inspect centrality <ORG_ID> [--min-degree 0.1] [--limit 50] [--json]
```

### Playbook

```bash
# Playbook targets (default) or outcomes
sable-platform inspect playbook <ORG_ID> [--json]
sable-platform inspect playbook <ORG_ID> --outcomes [--json]
sable-platform inspect playbook <ORG_ID> --limit 5
```

### Prospect Scores

```bash
# Lead Identifier prospect scores — defaults to latest run date
sable-platform inspect prospects [--min-score 0.5] [--tier "Tier 1"|"Tier 2"|"Tier 3"] [--run-date 2026-04-01] [--limit 50] [--json]
```

### Operational

```bash
# AI spend per org with budget headroom
sable-platform inspect spend [--org <ORG_ID>] [--json]

# Operator audit log
sable-platform inspect audit [--org tig] [--actor operator] [--action tag_deactivate] [--since 2026-04-01T00:00:00] [--limit 100] [--json]
```

---

## alerts — Proactive Alerting

### View & Manage

```bash
sable-platform alerts list [--org tig] [--severity critical|warning|info] [--status new|acknowledged|resolved] [--limit 20] [--json]
# Note: --status defaults to "new" — use --status acknowledged or --status resolved to see past alerts
sable-platform alerts acknowledge <ALERT_ID> [--operator sieggy]
sable-platform alerts evaluate [--org tig]   # Run all 12 checks for one or all orgs
sable-platform alerts mute <ORG_ID>          # Suppress delivery for an org
sable-platform alerts unmute <ORG_ID>        # Re-enable delivery
```

### Configure Delivery

```bash
sable-platform alerts config set --org tig --min-severity warning --cooldown-hours 4
sable-platform alerts config set --org tig --telegram-chat-id 123456789
sable-platform alerts config set --org tig --discord-webhook https://discord.com/api/webhooks/...
sable-platform alerts config set --org tig --disable
sable-platform alerts config show --org tig
```

### Alert Types (12 checks)

| Check | Severity | Fires When |
|-------|----------|------------|
| `stale_tracking` | warning | Tracking data older than threshold |
| `cultist_tag_expiring` | warning | Cultist tag about to expire |
| `sentiment_shift` | warning/critical | Entity sentiment change detected |
| `mvl_score_change` | warning | MVL score change |
| `unclaimed_actions` | info | Actions pending assignment |
| `workflow_failures` | warning | Workflow run failures (last 30 days) |
| `discord_pulse_regression` | warning | Discord pulse retention delta < -0.05 |
| `discord_pulse_stale` | warning | Discord pulse data older than 7 days |
| `stuck_runs` | warning | Runs stuck in 'running' > 2 hours |
| `member_decay` | warning/critical | Decay score >= 0.6 (critical if >= 0.8 + important tag) |
| `bridge_decay` | critical | Bridge entity with high centrality + high decay |
| `watchlist_changes` | warning/critical | Watched entity state changed between snapshots |

---

## actions — Operator Action Queue

```bash
sable-platform actions list --org tig [--status pending|claimed|completed|skipped] [--limit 50] [--json]
sable-platform actions create --org tig --title "DM top contributor" --type dm_outreach [--entity ENT_ID] [--description "..."] [--source manual]
sable-platform actions claim <ACTION_ID> --operator sieggy
sable-platform actions complete <ACTION_ID> [--notes "Done, positive response"]
sable-platform actions skip <ACTION_ID> [--notes "Not relevant"]
sable-platform actions summary --org tig
```

**Action types:** `dm_outreach`, `post_content`, `reply_thread`, `run_ama`, `general`
**Sources:** `playbook`, `strategy_brief`, `pulse_meta_recommendation`, `manual`

---

## outcomes — Track Results

```bash
sable-platform outcomes record --org tig --type client_signed [--action ACTION_ID] [--entity ENT_ID] [--notes "..."] [--operator sieggy]
sable-platform outcomes list --org tig [--type metric_change] [--limit 20]
sable-platform outcomes diagnostic-delta --org tig [--run RUN_ID]
```

**Outcome types:** `client_signed`, `client_churned`, `entity_converted`, `metric_change`, `dm_response`, `content_performance`, `general`

---

## journey — Entity Lifecycle

```bash
sable-platform journey show <ENTITY_ID>              # Full timeline for one entity
sable-platform journey funnel --org tig               # Aggregate funnel
sable-platform journey first-seen --org tig [--source cult_doctor|sable_tracking|pulse_meta|manual] [--limit 20]
sable-platform journey top --org tig                  # Top 5 most event-rich entity journeys
sable-platform journey top --org tig --limit 10       # Expand to top 10
sable-platform journey top --org tig --json           # Machine-readable (entity_id, display_name, event_count, events[])
```

`journey top` is the primary feed for SableWeb's `key_journeys` field. Entities are ranked by total event count (tag history + actions + outcomes). Returns full `get_entity_journey()` output for each.

---

## dashboard — Operator Overview

```bash
sable-platform dashboard              # What needs attention across all orgs
sable-platform dashboard --org tig    # Single org view
sable-platform dashboard --json       # JSON output
```

Shows: alerts by severity, stale syncs, stuck runs, pending actions, budget usage, decay risk — sorted by urgency.

---

## watchlist — Entity Monitoring

```bash
sable-platform watchlist add <ORG_ID> <ENTITY_ID> [--note "Key contributor, watch for churn"]
sable-platform watchlist remove <ORG_ID> <ENTITY_ID>
sable-platform watchlist list <ORG_ID> [--json]
sable-platform watchlist changes <ORG_ID> [--json]      # Recent changes for watched entities
sable-platform watchlist snapshot <ORG_ID>               # Take fresh snapshots now
```

Watchlist uses snapshot-based change detection — `snapshot` captures current state, `changes` diffs against previous snapshots.

---

## webhooks — Event Subscriptions

```bash
sable-platform webhooks add <ORG_ID> --url https://example.com/hook --events "alert.created,workflow.completed" [--secret mysecretkey1234567] [--generate-secret]
sable-platform webhooks list <ORG_ID> [--json]
sable-platform webhooks remove <SUBSCRIPTION_ID>
sable-platform webhooks test <ORG_ID> <SUBSCRIPTION_ID>   # Send test event
```

- Secrets must be >= 16 characters. Use `--generate-secret` for auto-generation.
- Webhooks sign payloads with HMAC-SHA256 (`X-Sable-Signature` header).
- Auto-disabled after 10 consecutive delivery failures.
- SSRF-hardened: localhost, private IPs, IPv6 loopback, link-local addresses are blocked.

---

## health-server — Programmatic Health Endpoint

Serves `GET /health` as JSON on a configurable port. Blocks until killed.

```bash
sable-platform health-server             # Listen on :8765 (default)
sable-platform health-server --port 9000 # Custom port
```

Response body: `{"ok": true, "migration_version": 30, "org_count": 2, "last_alert_eval_age_hours": 1.2, "alert_eval_stale": false, ...}`

Returns HTTP 200 on success, HTTP 404 for any path other than `/health`.

---

## metrics — Prometheus Metrics Export

Prints Prometheus text format metrics to stdout. Pipe to a scrape endpoint or file.

```bash
sable-platform metrics                   # Print metrics to stdout
```

Metrics exported:

| Metric | Type | Description |
|--------|------|-------------|
| `sable_active_orgs` | gauge | Number of active orgs |
| `sable_workflow_runs_total{status}` | counter | Total workflow runs by status |
| `sable_alerts_total{severity,status}` | gauge | Current alerts by severity and status |
| `sable_last_alert_eval_age_seconds` | gauge | Seconds since last alert evaluation (-1 if never run) |
