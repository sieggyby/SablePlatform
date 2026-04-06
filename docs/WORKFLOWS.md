# Workflows

SablePlatform ships five builtin deterministic workflows. Each run is fully durable — every step's input, output, error, and timing is recorded in `sable.db`. Runs can be resumed after interruption.

---

## Workflow 1: `prospect_diagnostic_sync`

**Purpose:** Take a qualified prospect through the full suite path — diagnosis → entity sync → artifact registration — and make every step observable.

**Config:**
```
prospect_yaml_path: str   # absolute path to CultGrader prospect config YAML
org_id: str               # sable.db org_id
```

**State transitions:**

```
START
  │
  ▼
validate_prospect         Load YAML, check required fields, verify org_id in DB
  │
  ▼
request_diagnostic        Run CultGraderAdapter → writes diagnostic_runs row
  │
  ▼
poll_diagnostic           Check run_meta.json exists at checkpoint_path
  │  ← if not done: step FAILS, run FAILS
  │    operator runs: sable-platform workflow resume <run_id>
  │
  ▼
verify_entity_sync        Query diagnostic_runs + entities table for confirmation
  │
  ▼
register_artifacts        Write report artifacts to artifacts table
  │
  ▼
mark_complete             Return summary
  │
  ▼
COMPLETED
```

**Key observability queries:**
```sql
-- When was this project discovered?
SELECT started_at FROM workflow_steps WHERE run_id=? AND step_name='validate_prospect';

-- When was it diagnosed?
SELECT completed_at FROM diagnostic_runs WHERE cult_run_id=?;

-- What artifacts were created?
SELECT output_json FROM workflow_steps WHERE run_id=? AND step_name='register_artifacts';

-- What failed?
SELECT step_name, error FROM workflow_steps WHERE run_id=? AND status='failed';
```

**CLI:**
```bash
sable-platform workflow run prospect_diagnostic_sync \
  --org my_org \
  --config prospect_yaml_path=/path/to/config.yaml

# After CultGrader completes:
sable-platform workflow resume <run_id>

sable-platform workflow status <run_id>
```

**Note on poll_diagnostic:** CultGrader runs take 5–20 minutes and run in their own process. The `poll_diagnostic` step checks for completion. If not done, the step fails (intentionally) and the run is paused. The operator resumes when ready. This is a deliberate trade-off — no background poller in v1.

---

## Workflow 2: `weekly_client_loop`

**Purpose:** Unify the recurring client workflow — check data freshness, refresh stale data, trigger strategy generation.

**Config:**
```
org_id: str                       # sable.db org_id
tracking_staleness_days: int = 7  # age threshold for tracking sync
pulse_staleness_days: int = 14    # age threshold for pulse artifacts
```

**State transitions:**

```
START
  │
  ▼
check_tracking_freshness  Query sync_runs → tracking_fresh: bool, tracking_age_days: int
  │
  ▼
check_pulse_freshness     Query artifacts → pulse_fresh: bool, pulse_age_days: int
  │
  ▼
mark_stale_artifacts      mark_artifacts_stale(conn, org_id, ["twitter_strategy_brief", "discord_playbook"])
  │  skip_if: tracking_fresh == True
  │
  ▼
trigger_tracking_sync     SableTrackingAdapter.run(org_id)
  │  skip_if: tracking_fresh == True
  │
  ▼
trigger_strategy_generation   SlopperAdvisoryAdapter.run(org_id)
  │
  ▼
register_artifacts        Count non-stale artifacts from DB
  │
  ▼
register_actions          Parse playbook + strategy brief → create action rows
  │
  ▼
evaluate_alerts           Run all alert checks for this org
  │
  ▼
mark_complete             Return freshness summary
  │
  ▼
COMPLETED
```

**skip_if logic:** `mark_stale_artifacts` and `trigger_tracking_sync` are skipped when `tracking_fresh=True`. The step is recorded in the DB with `status='skipped'` so the operator can see exactly what ran.

**Key observability queries:**
```sql
-- Is this client's data fresh?
SELECT output_json FROM workflow_steps WHERE run_id=? AND step_name='check_tracking_freshness';

-- What steps ran this week?
SELECT step_name, status, started_at FROM workflow_steps WHERE run_id=?;

-- What outputs were generated?
SELECT output_json FROM workflow_steps WHERE run_id=? AND step_name='register_artifacts';

-- Were any sources stale?
SELECT output_json FROM workflow_steps WHERE run_id=? AND step_name='mark_stale_artifacts';
```

**CLI:**
```bash
sable-platform workflow run weekly_client_loop --org my_org

# With custom staleness thresholds:
sable-platform workflow run weekly_client_loop \
  --org my_org \
  --config tracking_staleness_days=5 \
  --config pulse_staleness_days=10

sable-platform workflow status <run_id>
sable-platform workflow events <run_id>
```

---

## Workflow 3: `alert_check`

**Purpose:** Evaluate all proactive alert conditions across all orgs and deliver via Telegram/Discord.

**Config:** None required.

**Key details:**
- Runs 12 `_check_*` functions covering: tracking stale, cultist tag expiring, sentiment shift, MVL score change, unclaimed actions, workflow failures, discord pulse regression, discord pulse stale, stuck runs, member decay, bridge decay, watchlist changes.
- Each check writes an alert row; delivery is gated by per-`dedup_key` cooldown (`cooldown_hours`, default 4).
- Delivery channels: Telegram (`SABLE_TELEGRAM_BOT_TOKEN` + org `telegram_chat_id`) and Discord (webhook).
- Alert DB records are always written; only external HTTP delivery is suppressed during cooldown.

**CLI:**
```bash
sable-platform workflow run alert_check --org <org_id>
```

See `docs/ALERT_SYSTEM.md` for the full alert lifecycle, all 12 check descriptions, dedup key formats, per-org threshold overrides, and delivery channel setup.

---

## Workflow 4: `lead_discovery`

**Purpose:** Run the LeadIdentifierAdapter, sync results to the DB, and register artifacts.

**Config:**
```
org_id: str   # sable.db org_id
```

**State transitions:**
```
START → run_lead_identifier → create_entities → sync_scores → register_artifacts → mark_complete → COMPLETED
```

**CLI:**
```bash
sable-platform workflow run lead_discovery --org <org_id>
```

---

## Workflow 5: `onboard_client`

**Purpose:** 6-step onboarding sequence for a new org.

**Config:**
```
org_id: str   # sable.db org_id (must already exist)
```

**State transitions:**
```
START
  │
  ▼
verify_org              Raises SableError(ORG_NOT_FOUND) if org does not exist in DB
  │
  ▼
verify_tracking         SableTrackingAdapter health check (non-blocking — failure captured in tools_failed)
  │
  ▼
verify_slopper          SlopperAdvisoryAdapter health check (non-blocking)
  │
  ▼
verify_cult_grader      CultGraderAdapter health check (non-blocking)
  │
  ▼
create_initial_sync_record   Write seed sync_runs row
  │
  ▼
mark_complete           Return summary including tools_failed list
  │
  ▼
COMPLETED
```

**Note:** Adapter verification steps are non-blocking. A failed adapter check is recorded in `tools_failed` in the step output but does not fail the run. Only `verify_org` is blocking.

**CLI:**
```bash
sable-platform workflow run onboard_client --org <org_id>
```

---

## Adding new workflows

1. Create `sable_platform/workflows/builtins/my_workflow.py`
2. Define step functions returning `StepResult`
3. Build a `WorkflowDefinition` with `StepDefinition` list
4. Call `registry.register(MY_WORKFLOW)` at module level
5. Import the module in `registry._auto_register()` (or via direct import)

The workflow is then available as:
```bash
sable-platform workflow run my_workflow --org <org_id>
```

---

## Workflow DB tables (migrations 006–012, extended through 023)

```sql
workflow_runs (run_id, org_id, workflow_name, workflow_version, status, config_json,
               started_at, completed_at, error, created_at,
               step_fingerprint TEXT)  -- migration 012: sha1[:8] of sorted step names; NULL on pre-012 runs (validation skipped)

workflow_steps (step_id, run_id, step_name, step_index, status, retries,
                input_json, output_json, error, started_at, completed_at)

workflow_events (event_id, run_id, step_id, event_type, payload_json, created_at)
```

**Status values:** `pending | running | completed | failed | skipped | cancelled`

**Event types:** `run_started | run_resumed | run_completed | run_failed | step_started | step_completed | step_failed | step_skipped`
