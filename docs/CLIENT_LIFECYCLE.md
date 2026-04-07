# Client Lifecycle

Each Sable client passes through six stages. This document maps each stage to the CLI commands that drive it and the SableWeb views that surface it.

## Stages

### 1. Discovered

Lead Identifier found a prospect matching Sable's scoring criteria.

**How it happens:** `sable-platform workflow run lead_discovery --org <org>` (or the `lead_discovery` cron preset running weekly). The Lead Identifier adapter scores prospects and syncs results to `prospect_scores`.

**Inspect:** `sable-platform inspect prospect_pipeline`

**SableWeb view:** `/ops` — Prospect Pipeline tab. Shows all scored prospects with composite score, tier, and diagnostic status.

---

### 2. Diagnosed

Cult Grader ran a full diagnostic on the prospect.

**How it happens:**
- Automatically via the `trigger_cult_grader_for_tier1` step in `lead_discovery` (Tier 1 prospects, max 10 per run).
- Manually: `sable-platform workflow run prospect_diagnostic_sync --org <org>`

**Inspect:** `sable-platform inspect prospect_pipeline --tier "Tier 1"` (shows fit_score and diagnostic date).

**SableWeb view:** `/ops` — Prospect Pipeline tab, "Diagnosed" filter. Diagnostic PDF available for download.

---

### 3. Outreach

Operator has contacted the prospect using the diagnostic PDF as the conversation hook.

**How it happens:** Manual. The operator downloads the diagnostic PDF from SableWeb or the Cult Grader output directory and sends it to the prospect's team.

**Inspect:** `sable-platform inspect audit --action outreach_sent` (if logged).

**SableWeb view:** `/ops` — Prospect Pipeline tab, "Outreach" status column.

---

### 4. Onboarding

Client has signed and the Sable team is setting up infrastructure.

**How it happens:** `sable-platform workflow run onboard_client --org <org>`

This workflow:
- Creates the org in `sable.db`
- Sets up initial entity roster from Discord/Twitter handles
- Configures alert thresholds and delivery channels
- Runs first SableTracking sync
- Graduates the prospect (`prospect_scores.graduated_at` stamped)

**Inspect:** `sable-platform inspect orgs` (org appears with status `active`).

**SableWeb view:** `/client` — Client Portal. New client appears in the sidebar.

---

### 5. Active

Recurring workflows are running for the client.

**How it happens:** `sable-platform workflow run weekly_client_loop --org <org>` (typically via cron).

The weekly client loop runs:
1. SableTracking sync (contributor data refresh)
2. Discord pulse check
3. Decay score computation
4. Centrality analysis
5. Alert evaluation and delivery
6. Strategy brief generation (via Slopper)

**Inspect:**
- `sable-platform inspect health <org>` — sync freshness, open alerts, discord pulse, recent workflows
- `sable-platform inspect freshness <org>` — data age indicators
- `sable-platform dashboard` — urgency-sorted attention view across all orgs

**SableWeb view:** `/client` — Client Portal. Dashboard shows health metrics, alerts, decay heatmap, and key journeys.

---

### 6. Monitoring

Ongoing steady-state operations.

**How it happens:**
- `sable-platform alerts evaluate --org <org>` (or `--all-orgs`)
- `sable-platform dashboard`
- Cron presets: `alert_check` (every 4h), `backup` (daily), `gc` (weekly)

**Inspect:**
- `sable-platform inspect health <org>`
- `sable-platform inspect decay <org>`
- `sable-platform inspect centrality <org>`
- `sable-platform watchlist changes <org>`

**SableWeb view:**
- `/client` — Client Portal (per-org health and alerts)
- `/ops` — Operator Dashboard (cross-org attention view)

---

## Stage-to-View Summary

| Stage | CLI Command | SableWeb View |
|-------|-------------|---------------|
| Discovered | `workflow run lead_discovery` | `/ops` Prospect Pipeline |
| Diagnosed | `workflow run prospect_diagnostic_sync` | `/ops` Prospect Pipeline (diagnosed) |
| Outreach | Manual (diagnostic PDF) | `/ops` Prospect Pipeline (outreach) |
| Onboarding | `workflow run onboard_client` | `/client` Portal |
| Active | `workflow run weekly_client_loop` | `/client` Portal |
| Monitoring | `alerts evaluate`, `dashboard` | `/client` Portal, `/ops` Dashboard |
