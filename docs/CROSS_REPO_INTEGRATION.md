# Cross-Repo Integration Guide

How SablePlatform orchestrates the four workflow-engine-driven downstream repos, plus how SableKOL (a 5th specialized repo with a different integration pattern) coexists.

---

## Architecture Overview

```
                        ┌──────────────────────┐
                        │   SablePlatform CLI   │
                        │  sable-platform ...   │
                        └──────────┬───────────┘
                                   │
                        ┌──────────▼───────────┐
                        │   Workflow Engine     │
                        │  (synchronous, durable│
                        │   retry + resume)     │
                        └──────────┬───────────┘
              ┌────────────┬───────┴───────┬────────────┐
              ▼            ▼               ▼            ▼
     ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐
     │Cult Grader │ │  Slopper   │ │  Tracking  │ │   Lead ID  │
     │ diagnose.py│ │ sable ...  │ │ sync_runner│ │  main.py   │
     └────────────┘ └────────────┘ └────────────┘ └────────────┘

                    SableKOL (separate integration pattern)
                    ┌──────────────────────────────────┐
                    │  SableWeb /ops/kol-network/* ──→ │
                    │  FastAPI sidecar (compose-net)   │
                    │  reads/writes sable.db migs 032+ │
                    └──────────────────────────────────┘
```

SablePlatform never imports from downstream repos. The 4 workflow-engine integrations happen via subprocess adapters that shell out to each repo's CLI. **SableKOL is different**: it runs its own FastAPI service inside the SableWeb compose stack, owns `sable.db` migrations 032-041, and is invoked by SableWeb HTTP routes rather than by SP workflows. See its dedicated section below.

---

## Adapter Reference

### CultGraderAdapter

| Property | Value |
|----------|-------|
| Env var | `SABLE_CULT_GRADER_PATH` |
| Command | `python diagnose.py --config <prospect_yaml>` |
| Timeout | 3600s (1 hour) |
| Input | Prospect YAML file path |
| Output | Reads `diagnostic.json` + `run_meta.json` from checkpoint dir |
| Error | Non-zero exit code or missing `run_meta.json` |

**What flows back into sable.db:**
- Entities (community members) → `entities` + `entity_handles` tables
- Tags (cultist, voice, mvl, etc.) → `entity_tags` + `entity_tag_history`
- Diagnostic results → `diagnostic_runs` + `diagnostic_deltas`
- Decay scores → `entity_decay_scores`
- Centrality scores → `entity_centrality_scores`
- Interaction edges → `entity_interactions`
- Artifacts → `artifacts` table

### SableTrackingAdapter

| Property | Value |
|----------|-------|
| Env var | `SABLE_TRACKING_PATH` |
| Command | `python -m app.platform_sync_runner <org_id>` |
| Timeout | 600s |
| Input | `org_id` |
| Output | Latest `sync_runs` row |
| Error | Non-zero exit code or `sync_runs` status = `failed` |

**What flows back:** Entities discovered via tracking → `entities` + `entity_handles`.

### SlopperAdvisoryAdapter

| Property | Value |
|----------|-------|
| Env var | `SABLE_SLOPPER_PATH` |
| Command | `python -m sable advise <@handle>` |
| Timeout | 600s |
| Input | `org_id` (resolved to primary Twitter handle via `entity_handles`) |
| Output | `{status, job_ref, org_id}` — artifacts written directly to `sable.db` |

**Handle resolution:** The adapter resolves `org_id` → primary Twitter handle via `entity_handles` table (primary first, any non-archived fallback). Raises `SableError(INVALID_CONFIG)` if no handle found. Slopper's `sable advise` expects a Twitter handle, not an org_id.

**What flows back:** Strategy brief artifacts → `artifacts` table (type: `twitter_strategy_brief`). Slopper writes directly to `sable.db`; the adapter return value does not contain artifact paths.

### LeadIdentifierAdapter

| Property | Value |
|----------|-------|
| Env var | `SABLE_LEAD_IDENTIFIER_PATH` |
| Command | `python main.py run [--pass1-only]` |
| Timeout | 3600s |
| Input | Optional `pass1_only` flag (default: True) |
| Output | Reads `output/sable_leads_latest.json`, filters out `"pass"` (keeps `pursue` + `monitor`) |

**What flows back:** Lead prospects as entities.

**Automated Cult Grader trigger:** The `lead_discovery` workflow's `trigger_cult_grader_for_tier1` step automatically runs Cult Grader diagnostics for canonical Tier 1 prospects (composite >= 0.70 via `PURSUE_THRESHOLD`). Bounded to max 10 diagnostics per run. Each diagnostic is budget-checked via `check_budget()`. Individual diagnostic failures do not fail the workflow step — errors are logged and remaining prospects are processed.

---

## Prospect YAML Schema

The prospect YAML file is the entry point for all diagnostic workflows. It lives in Cult Grader's `prospects/` directory.

**Minimum required fields:**
```yaml
project_name: "SolStitch"
twitter_handle: "SolStitchXYZ"
```

**Full schema:**
```yaml
project_name: "SolStitch"
twitter_handle: "SolStitchXYZ"
tags: ["client"]                            # Filter with --tag in batch mode
token_ticker: "PSY"                         # Optional
sector: "L1 blockchain / ZK infrastructure" # Optional; affects B2B scoring thresholds
website: "https://solstitch.xyz/"                 # Optional
discord_invite: "https://discord.gg/..."    # Optional; used by Discord data collection
sable_org: "solstitch"                   # REQUIRED for platform sync — must match org_id in sable.db
operator_notes: |                           # Optional; injected into diagnostic context
  Privacy-first ZK L1. Stage: testnet.
  Funding: ~$9-10M. Investors: Blockchain Capital.
```

**Field naming:** `project_name` is the canonical field. `name` and `project_slug` are accepted as backward-compatible aliases — Platform normalizes them to `project_name` at validate time. New YAMLs should use `project_name` only.

**Critical:** `sable_org` must exactly match the `org_id` you created in sable.db. Without it, Cult Grader's platform sync (Stage 8 post-step) is silently skipped and no data flows back to SablePlatform.

---

## Data Flow: Lead Discovery → Diagnosis (Automated Pipeline)

The `lead_discovery` workflow automates the full prospecting pipeline:

```
sable-platform workflow run lead_discovery --org <org>

Steps:
1. validate_env          — check SABLE_LEAD_IDENTIFIER_PATH + org exists
2. run_lead_identifier   — execute Lead Identifier (pass-1)
3. parse_leads           — read sable_leads_latest.json, filter pursue+monitor
4. create_entities       — create/find entities, tag as bd_prospect
5. sync_scores           — upsert to prospect_scores table
6. trigger_cult_grader_for_tier1 — auto-diagnose Tier 1 (composite >= 0.70)
                           • Max 10 per run (cost bound)
                           • check_budget() before each
                           • Individual failures logged, not fatal
7. sync_cult_grader_results — log cost summary
8. register_artifacts    — register Lead Identifier output
9. evaluate_alerts       — run alert checks
10. mark_complete        — summary
```

Schedule via cron preset: `sable-platform cron add --preset lead_discovery --org <org>` (Monday 22:00 UTC).

---

## Data Flow: Client Onboarding (End to End)

Onboarding requires multiple workflow runs. `onboard_client` is a readiness check — it does not run diagnostics or sync data.

```
1. Create org in sable.db
   └─ sable-platform org create solstitch --name "SolStitch"

2. Create/update prospect YAML (in Cult Grader repo)
   └─ prospects/solstitch.yaml (must include sable_org: "solstitch")

3. Run onboard workflow (readiness check only)
   └─ sable-platform workflow run onboard_client --org solstitch \
        -c prospect_yaml_path=/path/to/solstitch.yaml
   └─ Steps: verify org exists, verify adapter env vars, create initial sync record, report readiness

4. Run diagnostic + sync (this is where data flows into sable.db)
   └─ sable-platform workflow run prospect_diagnostic_sync --org solstitch \
        -c prospect_yaml_path=/path/to/solstitch.yaml
   └─ Or run Cult Grader directly for more control:
      cd $SABLE_CULT_GRADER_PATH && python diagnose.py --config prospects/solstitch.yaml

5. (Optional) Run tracking sync and strategy generation
   └─ sable-platform workflow run weekly_client_loop --org solstitch
```

---

## Cult Grader Direct Commands

When you need more control than the workflow provides, call Cult Grader directly:

```bash
cd $SABLE_CULT_GRADER_PATH

# Standard diagnostic (~$1-2)
python diagnose.py --config prospects/solstitch.yaml

# Onboard mode: deep historical collection
python diagnose.py --config prospects/solstitch.yaml --mode onboard --onboard-since 2025-01-01 --cost-ceiling 20

# Re-run diagnostic + report only (~$0.07)
python diagnose.py --config prospects/solstitch.yaml --from-stage diag

# Re-render reports only (free)
python diagnose.py --config prospects/solstitch.yaml --from-stage report

# Batch all prospects
python diagnose.py --batch prospects/ --concurrency 3

# Batch filtered by tag
python diagnose.py --batch prospects/ --tag client

# Compare two runs
python diagnose.py --compare diagnostics/psy-protocol_SolStitchXYZ/ --runs 2026-03-01 2026-04-01

# Corpus overview
python diagnose.py --summary diagnostics/ --sort-by sable_fit_score
python diagnose.py --corpus-dashboard diagnostics/
python diagnose.py --cross-reference diagnostics/ --min-projects 3

# Trend analysis
python diagnose.py --trend diagnostics/psy-protocol_SolStitchXYZ/
```

**Key flags:**
| Flag | Notes |
|------|-------|
| `--from-stage <alias>` | `data`, `validate`, `research`, `metrics`, `classify`, `diag`, `cheat`, `report` |
| `--mode onboard` | Deep historical collection; ignores `--from-stage` |
| `--cost-ceiling N` | Hard SocialData spend cap (onboard only) |
| `--research-mode web` | Live web search + cheat sheet (~2x cost) |
| `--force` | Bypass cache + process suspect-quality projects |
| `--dry-run` | Validate configs, estimate cost |
| `--with-comparison` | Auto-compare vs previous run |

---

## Slopper Direct Commands

Content production and vault management:

```bash
cd $SABLE_SLOPPER_PATH
source .venv/bin/activate

# Strategy brief
sable advise <org_id>

# Vault management
sable vault init <org_id>              # Create Obsidian-compatible vault at ~/sable-vault/{org}/
sable vault sync <org_id>             # Sync platform data into vault
sable vault search <org_id> <query>   # Search vault content

# Content generation
sable write <@handle> "<topic>"       # Generate tweet variants
sable write <@handle> "<topic>" --reply-to <tweet_url>
sable score <@handle> "<draft>"       # Score a draft tweet

# Account operations
sable roster add <@handle> --org <org_id>
sable roster list [--org <org_id>]

# Performance tracking
sable pulse snapshot <@handle>        # Take performance snapshot
sable pulse report <@handle>          # Generate performance report
sable pulse meta <@handle>            # Format intelligence + topic gaps

# Content pipeline
sable clip process <youtube_url> [--account <@handle>]
sable meme generate --account <@handle> --topic "<topic>"
sable calendar generate <@handle>     # Posting calendar
sable diagnose <@handle>              # Full account audit

# Client onboarding (Slopper's own 6-step pipeline)
sable onboard <org_id>
```

---

## SableTracking Direct Commands

```bash
cd $SABLE_TRACKING_PATH

# Run platform sync for an org
SABLE_CLIENT_ORG_MAP='{"TIG":"tig"}' python -m app.platform_sync_runner tig
```

---

## SableKOL Integration

SableKOL has a different integration shape from the 4 workflow-engine repos above. It's invoked by:

1. **The operator (CLI)** — `sable-kol ingest / classify / crossref / find / regenerate` runs as a normal Python CLI on the operator's laptop or a Hetzner systemd timer. Bank ETL + outreach-plan + network-graph generation paths.
2. **SableWeb HTTP routes** — `/api/ops/kol-network/*` routes proxy operator actions (preflight, comparable suggest, reuse check, project create, per-candidate enrichment) to SableKOL's FastAPI sidecar (`sable_kol/preflight_service.py`) running at `http://sable-kol-preflight:8001` inside the compose network. xAI Grok + SocialData API keys live only on the sidecar.

**Tables SableKOL owns** (defined in SablePlatform migrations 032-041, written by SableKOL code):

| Table | Migration | Purpose |
|---|---|---|
| `kol_candidates` | 032 | Per-handle bank entry (one LIVE row per handle, partial unique index) |
| `kol_handle_resolution_conflicts` | 032 | Tracks unresolved-duplicate handles for operator-triage |
| `project_profiles_external` | 032 | Path-(ii) external project profiles (non-Sable orgs) |
| `kol_strength_score` extensions | 033 | Strength score + paid-enrichment fields |
| `kol_grok_enrich` extensions | 034 | Grok-derived columns (credibility, listed_count, etc.) |
| `kol_location` | 035 | location column on kol_candidates |
| `kol_platform_presence` | 036 | platform_presence_json column (cross-platform reach) |
| `kol_follow_edges` | 037 | Per-extract-run follower/following edges |
| `kol_operator_relationships` | 038 | Per-client operator tagging of candidates |
| `kol_extract_runs.client_id` | 039 | Client-scoping on bulk-fetch runs |
| `kol_create_audit` | 040 | Append-only audit log for /api/ops/kol-network/* hits |
| `kol_enrichment` | 041 | Per-(candidate, operator) Grok enrichment cache |

**SablePlatform's role.** SP owns the migration files + schema parity tests + the `cost_events` ledger SableKOL writes to. SableKOL imports `sable_platform.db.connection.get_db()` via `sable_kol.db.open_db()` and never opens its own connection. No SP workflow currently invokes SableKOL; the integration is one-way (SableKOL is a consumer of SP's DB connection factory + a writer to SP-owned tables).

**For details** see `SableKOL/CLAUDE.md` (architectural rules), `SableKOL/docs/ENRICHMENT.md` (the v2.5 per-candidate intel feature), and `SableKOL/docs/PERSONAS.md` (operator priming system).

---

## Error Handling

**Adapter failures do not crash the workflow.** Each adapter follows this pattern:
1. `run()` — executes subprocess synchronously (blocks until completion or timeout)
2. `status()` — checks result (exit code, output file existence)
3. `get_result()` — reads and parses output

If a step fails, the workflow run is marked `failed` and the specific step shows the error. Resume with:
```bash
sable-platform workflow resume <RUN_ID>
```

**Common failure modes:**
- Adapter env var not set → `FileNotFoundError` in adapter `run()`
- Downstream API key missing → subprocess exits non-zero
- SocialData cost ceiling hit (onboard mode) → run completes with partial data, not marked failed
- Timeout → adapter kills subprocess after timeout period

Check workflow events for details:
```bash
sable-platform workflow events <RUN_ID>
```

---

## Platform Sync Details

After a Cult Grader diagnostic completes, Stage 8 automatically syncs data to sable.db if `sable_org` is set in the prospect YAML. This sync:

1. Creates/updates entities from the community member dataset
2. Applies tags (cultist, voice, mvl, top_contributor, etc.)
3. Writes tag history entries
4. Syncs decay scores if present
5. Syncs centrality scores if present
6. Syncs interaction edges if reply pair data exists
7. Registers diagnostic artifacts

8. Syncs playbook targets and outcomes if present
9. Writes `run_summary_json` blob (F-BLOB v1: grades, scores, narratives, classification, decay, funnel, roster)

This is fire-and-forget — the Cult Grader run completes `"ok"` even if sync fails. Check `diagnostics/_error_log.jsonl` for sync errors.

---

## Cross-Repo Dependency: Importing from SablePlatform

Downstream repos can import Pydantic contracts and DB helpers from `sable_platform` as a pip dependency.

**Installation:**
```bash
# Editable install (development)
pip install -e /path/to/SablePlatform

# Or add to requirements.txt
-e /path/to/SablePlatform
```

**Available contracts for import:**
| Contract | Import path |
|----------|-------------|
| `TrackingMetadata` | `sable_platform.contracts.tracking` |
| `Lead`, `DimensionScores` | `sable_platform.contracts.leads` |
| `ProspectHandoff` | `sable_platform.contracts.leads` |
| `Entity`, `EntityHandle`, `EntityTag` | `sable_platform.contracts.entities` |
| `Alert` | `sable_platform.contracts.alerts` |
| `Artifact` | `sable_platform.contracts.artifacts` |

**Machine-readable JSON Schema:** Generated schemas for all 8 contracts live in `docs/schemas/`. Regenerate with:
```bash
SABLE_OPERATOR_ID=your_name sable-platform schema -o docs/schemas/
```

**Graceful import pattern (recommended):**
```python
try:
    from sable_platform.contracts.tracking import TrackingMetadata
    HAS_PLATFORM = True
except ImportError:
    HAS_PLATFORM = False
```

This pattern (used by Lead Identifier) lets the downstream repo function without SablePlatform installed — contract validation is a bonus, not a hard requirement.

**Who uses this today:**
- **Slopper:** Imports `sable_platform.db.connection`, `sable_platform.db.tags`, etc. (direct DB access for tag writes)
- **Lead Identifier:** Conditional import of contracts for sync validation
- **SableTracking:** Does not import from `sable_platform` yet (TRACK-5 pending)
