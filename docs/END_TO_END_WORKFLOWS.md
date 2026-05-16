# End-to-End Workflows

Step-by-step recipes for common operations using SablePlatform as the orchestration hub.

---

## 1. New Client Onboarding

**Goal:** Take a new project from zero to fully instrumented in sable.db with diagnostic data, vault, and monitoring.

### Step 1: Create the org

```bash
sable-platform org create solstitch --name "SolStitch"
```

### Step 2: Create prospect YAML

Create `$SABLE_CULT_GRADER_PATH/prospects/solstitch.yaml`:

```yaml
project_name: "SolStitch"
twitter_handle: "SolStitchXYZ"
tags: ["client"]
sable_org: "solstitch"    # Must match org_id from step 1
website: "https://solstitch.xyz/"
operator_notes: |
  Privacy-first ZK L1. Testnet stage.
```

### Step 3: Run onboard readiness check

`onboard_client` verifies the org, adapter env vars, and creates an initial sync record. It does **not** run diagnostics or sync data.

```bash
sable-platform workflow run onboard_client --org solstitch \
  -c prospect_yaml_path=$SABLE_CULT_GRADER_PATH/prospects/solstitch.yaml
```

### Step 4: Run diagnostic + sync

This is where data actually flows into sable.db. Choose one:

```bash
# Option A: Via workflow (standard diagnostic)
sable-platform workflow run prospect_diagnostic_sync --org solstitch \
  -c prospect_yaml_path=$SABLE_CULT_GRADER_PATH/prospects/solstitch.yaml

# Option B: Direct Cult Grader (more control — deep historical collection)
cd $SABLE_CULT_GRADER_PATH
python diagnose.py --config prospects/solstitch.yaml \
  --mode onboard --onboard-since 2025-01-01 --cost-ceiling 20
```

### Step 5: Initialize Obsidian vault

```bash
cd $SABLE_SLOPPER_PATH
source .venv/bin/activate
sable vault init solstitch
sable vault sync solstitch
```

### Step 6: Set up alerts

```bash
sable-platform alerts config set --org solstitch --min-severity warning --cooldown-hours 4
sable-platform alerts evaluate --org solstitch
```

### Step 7: Verify

```bash
sable-platform inspect health solstitch
sable-platform inspect entities solstitch --limit 10
sable-platform dashboard --org solstitch
```

---

## 2. Weekly Client Review

**Goal:** Refresh data, check health, generate strategy, handle alerts.

### Quick version (automated)

```bash
sable-platform workflow run weekly_client_loop --org tig
```

This checks data freshness, refreshes stale data, and generates a strategy brief.

### Manual version (full control)

```bash
# 1. Check what's stale
sable-platform inspect freshness tig

# 2. Re-run diagnostic if stale (from Cult Grader)
cd $SABLE_CULT_GRADER_PATH
python diagnose.py --config prospects/tigfoundation.yaml

# 3. Sync tracking data
cd $SABLE_TRACKING_PATH
python -m app.platform_sync_runner tig

# 4. Generate fresh strategy brief
cd $SABLE_SLOPPER_PATH
sable advise tig

# 5. Evaluate alerts
sable-platform alerts evaluate --org tig

# 6. Review dashboard
sable-platform dashboard --org tig
```

---

## 3. Morning Operator Check

**Goal:** 2-minute daily triage across all clients.

```bash
# Backup before anything else
sable-platform backup --label daily

# What needs attention right now?
sable-platform dashboard

# Any critical alerts?
sable-platform alerts list --severity critical --status new

# Any stuck workflows?
sable-platform workflow gc
sable-platform workflow list --org tig --limit 3
sable-platform workflow list --org solstitch --limit 3

# Preflight before running new workflows
sable-platform workflow preflight
```

---

## 4. Investigating a Community Member

**Goal:** Deep-dive on a specific entity — their journey, interactions, decay risk, and watchlist status.

```bash
# Find the entity
sable-platform inspect entities solstitch --limit 100 | grep -i "psychonaut"

# Full timeline
sable-platform journey show <ENTITY_ID>

# Their interaction graph
sable-platform inspect interactions solstitch --json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for edge in data:
    if '<handle>' in (edge.get('source_handle',''), edge.get('target_handle','')):
        print(edge)
"

# Decay risk
sable-platform inspect decay solstitch --json | python3 -c "
import json, sys
for e in json.load(sys.stdin):
    if e.get('entity_id') == '<ENTITY_ID>':
        print(json.dumps(e, indent=2))
"

# Centrality (are they a bridge node?)
sable-platform inspect centrality solstitch --json

# Add to watchlist for ongoing monitoring
sable-platform watchlist add solstitch <ENTITY_ID> --note "Key contributor, potential churn risk"
```

---

## 5. Diagnostic Comparison (Trend Analysis)

**Goal:** Compare two diagnostic runs to see what changed.

```bash
# From Cult Grader
cd $SABLE_CULT_GRADER_PATH

# Compare specific runs
python diagnose.py --compare diagnostics/psy-protocol_SolStitchXYZ/ --runs 2026-03-01 2026-04-01

# Or auto-compare latest two
python diagnose.py --compare diagnostics/psy-protocol_SolStitchXYZ/

# Trend report across all runs
python diagnose.py --trend diagnostics/psy-protocol_SolStitchXYZ/

# From SablePlatform — diagnostic deltas
sable-platform outcomes diagnostic-delta --org solstitch
```

---

## 6. Corpus-Level Analysis

**Goal:** Cross-project insights across all diagnosed prospects.

```bash
cd $SABLE_CULT_GRADER_PATH

# Summary table sorted by Sable fit score
python diagnose.py --summary diagnostics/ --sort-by sable_fit_score

# Cross-reference: find accounts active in 3+ projects
python diagnose.py --cross-reference diagnostics/ --min-projects 3

# Full corpus dashboard
python diagnose.py --corpus-dashboard diagnostics/

# Export all data
python diagnose.py --export diagnostics/
```

---

## 7. Content Production (via Slopper)

**Goal:** Generate content for a managed account.

```bash
cd $SABLE_SLOPPER_PATH
source .venv/bin/activate

# Generate tweets
sable write @SolStitchXYZ "testnet milestone announcement"

# Score a draft
sable score @SolStitchXYZ "The agents are ready. ZK-first from day one."

# Generate a posting calendar
sable calendar generate @SolStitchXYZ

# Performance snapshot
sable pulse snapshot @SolStitchXYZ
sable pulse report @SolStitchXYZ

# Format intelligence
sable pulse meta @SolStitchXYZ
```

---

## 8. Alert Response Workflow

**Goal:** Respond to an alert from triage to resolution.

```bash
# 1. See the alert
sable-platform alerts list --status new

# 2. Acknowledge it
sable-platform alerts acknowledge <ALERT_ID> --operator sieggy

# 3. Investigate based on alert type:

# For member_decay:
sable-platform inspect decay <ORG> --tier critical
sable-platform journey show <ENTITY_ID>

# For stale_tracking:
sable-platform inspect freshness <ORG>
sable-platform workflow run weekly_client_loop --org <ORG>

# For stuck_runs:
sable-platform workflow gc
sable-platform workflow status <RUN_ID>
sable-platform workflow resume <RUN_ID>

# 4. Record outcome if action was taken
sable-platform outcomes record --org <ORG> --type general --notes "Resolved stale tracking alert, re-ran sync"
```

---

## 9. Budget Monitoring

**Goal:** Track AI spend across orgs.

```bash
# Per-org spend and headroom
sable-platform inspect spend
sable-platform inspect spend --org tig --json

# Default cap is $5/week/org. Adjust in config:
# ~/.sable/config.yaml:
#   platform:
#     cost_caps:
#       max_ai_usd_per_org_per_week: 10.0
```

---

## 10. Vault Workflow (Obsidian)

**Goal:** Use Obsidian to browse and annotate client data.

```bash
cd $SABLE_SLOPPER_PATH
source .venv/bin/activate

# Initialize vault for a client
sable vault init tig

# Sync latest platform data into vault
sable vault sync tig

# Search vault
sable vault search tig "community health"

# Open in Obsidian: point Obsidian at ~/sable-vault/tig/
```

The vault is Obsidian-compatible markdown with YAML frontmatter. You can add your own notes, link between entities, and use Obsidian's graph view to visualize relationships.
