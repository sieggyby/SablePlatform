# SablePlatform — Roadmap

Future work only. For completed items, see [AUDIT_HISTORY.md](AUDIT_HISTORY.md).

See CLAUDE.md for project architecture, key files, and working conventions.

---

## Feature: Run Summary JSON Blob for SableWeb (F-BLOB) — SYNC COMPLETE

**SablePlatform side complete (2026-04-03):** Migration 021 adds `run_summary_json TEXT` column to `diagnostic_runs`.

**Cult Grader side complete (2026-04-04):** `_build_run_summary()` in `platform_sync.py` assembles versioned JSON blob (schema_version: 1, 50KB cap with progressive trimming). `_upsert_diagnostic_run()` passes `run_summary_json`. Enriched with INT-3/4/5/6: confidence band + dimensions, decay summary, fragility score + funnel, roster snapshot (capped at 20 handles). 10 tests.

**Remaining:**
- **SableWeb:** Consume `run_summary_json` in dashboard views.

---

## Feature: Playbook Outcome Tagging Tables (F-PBTAG) — SYNC COMPLETE

**SablePlatform side complete (2026-04-03):** Migration 022 adds `playbook_targets` and `playbook_outcomes` tables. DB helpers in `db/playbook.py`: `upsert_playbook_targets()`, `get_latest_playbook_targets()`, `list_playbook_targets()`, `record_playbook_outcomes()`, `get_latest_playbook_outcomes()`, `list_playbook_outcomes()`.

**Cult Grader side complete (2026-04-04):** `_sync_playbook_data()` in `platform_sync.py` loads `playbook_targets.json` and `playbook_outcomes.json` from the run directory, calls `upsert_playbook_targets()` and `record_playbook_outcomes()`. Non-fatal. 5 tests.

**Remaining:**
- **SableWeb:** Surface playbook target/outcome data in client dashboards.

---

## Feature: Entity Interaction Edge Table — SYNC COMPLETE

**Data layer:** Migration 014, `db/interactions.py`, `inspect interactions` CLI — all landed.

**Sync wired (2026-04-03):** Cult Grader `platform_sync.py:_sync_interaction_edges()` calls `sync_interaction_edges()` with pre-aggregated reply pairs (deduped by `(src, tgt)`, counts accumulated, timestamp ranges tracked). Non-fatal. 17 tests in Cult Grader.

**Remaining:**
- **SableWeb relationship web:** Graph rendering lives in SableWeb. See `SableWeb/docs/TODO_product_review.md` — Session 4 Addendum.

---

## Feature: Churn Prediction — SYNC COMPLETE

**Data layer + alerting:** Migration 015, `db/decay.py`, `_check_member_decay()` alert, `inspect decay` CLI — all landed.

**Sync wired (2026-04-03):** Cult Grader `platform_sync.py:_sync_decay_scores()` calls `sync_decay_scores()` with risk level mapping (`stable`→`low`, `at_risk`→`medium`, `high_churn_risk`→`high`). Includes `factors_json` with pattern, confidence, and posting_slope. Non-fatal. Slopper CHURN-1/CHURN-2 also shipped.

**Remaining:**
- **SableWeb decay dashboard:** Visualization of at-risk members lives in SableWeb.

---

## Feature: Network Centrality — SYNC COMPLETE

**Schema aligned (2026-04-03):** Migration 023 adds `in_centrality`/`out_centrality` columns matching Cult Grader output. `degree_centrality` computed as average of in+out. Bridge decay alert uses `degree_centrality`. Betweenness/eigenvector columns retained (legacy, unused — SQLite can't drop columns).

**Cult Grader side complete (2026-04-04):** `_sync_centrality_scores()` in `platform_sync.py` builds interaction graph from reply pairs, excludes project + team handles, passes `in_centrality`/`out_centrality` to `sync_centrality_scores()`. Non-fatal. 6 tests.

**Remaining:**
- **SableWeb:** Graph rendering for relationship web visualization.

---

## SablePlatform-Side Fixes (from adversarial review 2026-04-04) — ALL COMPLETE

### ✅ BUG FIX: `SlopperAdvisoryAdapter` handle resolution (2026-04-04)
`_resolve_primary_handle()` resolves org → primary Twitter handle via `entity_handles` before subprocess call. Falls back to any Twitter handle if no primary. 6 tests.

### ✅ `_sync_scores` step in `lead_discovery` workflow (2026-04-04)
Maps leads to `prospect_scores` with dimension inversion (community_gap → community_health, conversation_gap → language_signal). `max_retries=0` (non-fatal). 7 tests.

### ✅ Extended `_register_actions` for strategy briefs (2026-04-04)
Refactored into `_parse_actions_from_artifact()` + `_register_actions()`. Parses both `discord_playbook` (action_type=general) and `twitter_strategy_brief` (action_type=post_content). 6 tests.

### ✅ Dual-source pulse freshness check (2026-04-04)
`_check_pulse_freshness` now queries both `sync_runs` (pulse_track, meta_scan) and `artifacts` (pulse_report, meta_report), using most recent. 6 tests.

### ✅ `twice-weekly` cron preset (2026-04-04)
Monday + Thursday 06:00 UTC. 3 tests.

### ✅ `add_entity_note()` + `list_entity_notes()` helpers (2026-04-04)
CRUD for `entity_notes` table in `db/entities.py`. 9 tests.

---

## Feature: Lead Identifier → sable.db Prospect Score Sync — DIRECT SYNC COMPLETE, WORKFLOW SYNC BROKEN

**Data layer complete:** Migration 020, `db/prospects.py`, `inspect prospects` CLI command — all landed. See CLAUDE.md § Prospect Scoring.

**Lead Identifier direct sync complete (2026-04-04):** `platform_sync.py:sync_scores_to_platform()` and `sync_cost_to_platform()` shipped with 11 tests. Wired into `main.py run --sync`, `rescore --sync`, and `ingest-inbound --sync`. This path works correctly.

**Workflow sync path broken:** `lead_discovery` workflow's `_parse_leads` → `_sync_scores` path has two critical bugs (LI-1, LI-2 below). The adapter filters on a non-existent field (returns empty list) and the sync step reads non-existent fields from the Lead contract (writes empty dimensions). See § Lead Identifier Integration Fixes below.

**Remaining:**
- **SablePlatform side:** Fix LI-1 and LI-2 (see below)
- **Consumer:** SableWeb `data-service.ts:assembleProspects()` (see `SableWeb/TODO.md § Backend Implementation Plan`)

---

## Lead Identifier Integration Fixes (from cross-repo adversarial audit 2026-04-04)

Production-readiness audit found two critical bugs and two data-flow gaps in the Lead Identifier → SablePlatform workflow path. The direct sync path (`platform_sync.py` called via `main.py --sync`) works correctly. The workflow path (`lead_discovery` → `LeadIdentifierAdapter` → `_sync_scores`) produces garbage data.

---

### LI-1. CRITICAL — `LeadIdentifierAdapter.get_result()` Filters on Non-Existent Field

**File:** `sable_platform/adapters/lead_identifier.py:60-61`

**Bug:** The adapter filters leads by `recommended_action != "pursue"`. Lead Identifier's JSON output never contains a `recommended_action` field. Every lead defaults to `"monitor"` and gets filtered out. `get_result()` always returns `{"leads": []}`.

**Consequence:** The entire `lead_discovery` workflow produces zero leads → zero entities → zero scores. Completes "successfully" with all-zero outputs.

**Fix (two-sided):**

1. **Lead Identifier side (their TODO §8D):** Adding `recommended_action` field to JSON output (`"pursue"` / `"monitor"` / `"pass"` mapped from tier thresholds 0.70 / 0.55).

2. **SablePlatform side:**
   - If `recommended_action` is present, use it; if absent, derive from `composite_score` (same thresholds: `>= 0.70` → pursue, `>= 0.55` → monitor, else pass)
   - Filter out `"pass"` only (Tier 3 noise). Keep Tier 1 (`"pursue"`) and Tier 2 (`"monitor"`) — both need entities and scores for the SableWeb triage view.

**Taxonomy note:** `Lead` contract uses `recommended_action` (`"pursue"` / `"monitor"` / `"pass"`). `prospect_scores` uses `tier` (`"Tier 1"` / `"Tier 2"` / `"Tier 3"`). The `_sync_scores` step must derive `tier` from `composite_score` directly (same 0.70 / 0.55 thresholds), not from `recommended_action`.

**Tests:**
1. `get_result()` with `recommended_action` present → returns Tier 1 + Tier 2, excludes Tier 3
2. `get_result()` with `recommended_action` absent → derives from composite_score
3. `get_result()` with empty leads → `{"leads": []}`
4. Threshold boundaries (composite = 0.55, 0.70) → deterministic
5. Unknown `recommended_action` value → treated as `"pass"`

---

### LI-2. CRITICAL — `_sync_scores` Reads Non-Existent Fields from Lead Contract

**File:** `sable_platform/workflows/builtins/lead_discovery.py:136-179`

**Bug:** `_sync_scores` reads `lead.get("dimensions", {})`, `tier`, `stage`, `rationale`, `enrichment`, `next_action` — none exist on the `Lead` contract (`contracts/leads.py`, 9 fields). Writes empty `dimensions={}`, `tier="monitor"`, `None` for everything else.

**Additionally:** The dimension mapping diverges from `platform_sync.py`:
- `_sync_scores`: 4 dimensions including non-existent `content_quality`
- `platform_sync.py`: 5 dimensions — `community_health`, `language_signal`, `growth_trajectory`, `engagement_quality`, `sable_fit`

**Fix:** Enrich the `Lead` contract with typed dimensions:

```python
class DimensionScores(BaseModel):
    community_health: float = 0.5      # 1.0 - community_gap
    language_signal: float = 0.5       # 1.0 - conversation_gap
    growth_trajectory: float = 0.5     # tge_proximity (not inverted)
    engagement_quality: float = 0.5    # 1.0 - engagement_gap
    sable_fit: float = 0.5            # composite (pass-through)

class Lead(BaseModel):
    # ... existing 9 fields ...
    tier: str = "Tier 3"
    stage: str = "lead"
    dimensions: DimensionScores = DimensionScores()
    rationale: Optional[dict] = None
    enrichment: Optional[dict] = None
    next_action: Optional[str] = None
```

Update `LeadIdentifierAdapter.get_result()` to populate these from raw JSON (same inversion as `platform_sync.py`). Update `_sync_scores` to read from typed `dimensions`, drop `content_quality`, add `engagement_quality` + `sable_fit`.

**Contract change is backwards-compatible** — all new fields have defaults. Known consumers: `LeadIdentifierAdapter.get_result()`, `_create_entities`, `_sync_scores`. No external breakage.

**Tests:**
1. End-to-end: `_parse_leads` → `_sync_scores` → `prospect_scores` rows have all 5 dimension keys
2. Gap scores 0.0 → dimensions 1.0 (inversion at boundary)
3. Gap scores `None` → dimensions 0.5 (neutral default)
4. Empty `project_id` + empty `name` → skip or error, not silent empty-string `org_id`
5. Tier derivation matches `platform_sync.py` (0.70 / 0.55)

---

### LI-3. HIGH — Prospect Lifecycle: No Graduation Mechanism

**Current state:** When a prospect converts to a client, Lead Identifier continues scoring it. `prospect_scores` accumulates duplicate rows. No "graduated" marker exists.

**Note:** `onboard_client` is a preflight/env-check workflow (verifies adapters, creates a pending `sync_runs` row). It does **not** transition entity status or touch `prospect_scores`. There is no lifecycle transition point in the current codebase.

**Also:** `prospect_scores.org_id` stores the *prospect project's* ID (e.g., `"aethir"`), not the Sable client org_id. Graduation queries must match on project_id, not `ctx.org_id`.

**Recommended fix — CLI command (Option A):**
- `sable-platform org graduate <prospect_project_id>` stamps `graduated_at` on matching rows
- Operator runs this manually when a prospect converts
- Simple, explicit, no workflow coupling

**Migration 025:** `ALTER TABLE prospect_scores ADD COLUMN graduated_at TEXT`

**DB changes:**
- `list_prospect_scores()`: filter `WHERE graduated_at IS NULL` by default; add `include_graduated: bool = False`
- `inspect prospects` CLI: `--include-graduated` flag

**Tests:**
1. `list_prospect_scores()` excludes graduated by default
2. `list_prospect_scores(include_graduated=True)` returns all
3. CLI `org graduate` stamps matching rows
4. CLI `org graduate` on non-existent project_id → clear error

---

### LI-4. MEDIUM — Two Divergent Sync Paths Should Converge

Two paths write to `prospect_scores`:
1. **Direct:** Lead Identifier's `platform_sync.py` (correct, 5 dimensions)
2. **Workflow:** `lead_discovery._sync_scores()` (broken, wrong 4 dimensions)

After LI-1/LI-2 are fixed, decide which is canonical. If workflow is primary, deprecate direct path. If direct remains primary, have workflow delegate to `platform_sync.py` via subprocess.

**No code change now.** Design decision after LI-1/LI-2. Re-evaluate in the first PR after LI-1+LI-2 merge — if not addressed, convert to a concrete task or delete.

---

## Production Infrastructure (from 2026-04-04 suite audit)

SableWeb production readiness audit (3.5/10) identified cross-repo gaps. These items are SablePlatform's share. Ordered by execution priority.

**Recommended execution order:** SP-LOCK → SP-AUTH + SP-IDX (shared migration 024) → SP-OBS Phase 1 → SP-DEPLOY → SP-1 → SP-WEBHOOK → SP-RETENTION → SP-2 → SP-3 → SP-4

**Migration numbering:** SP-AUTH `operator_id` column + SP-IDX compound index = migration 024. LI-3 `graduated_at` column = migration 025. Do not combine — they may land at different times.

### SP-LOCK: Workflow execution locking — CRITICAL [M]

**Tier 1 per AGENTS.md** — breaks prod, corrupts data.

**File:** `sable_platform/workflows/engine.py`

**Current:** No locking prevents two concurrent workflow runs on the same org. Two `sable-platform workflow run weekly_client_loop --org tig` invocations will interleave step execution, corrupt step state, and produce duplicate alerts/entities.

**Fix:** At `WorkflowRunner.run()` entry, check `workflow_runs` for an existing `in_progress` run on the same `(org_id, workflow_name)`. Refuse to start if one exists, raising `SableError(WORKFLOW_ALREADY_RUNNING)`. This is purely in-DB, visible via `workflow list`, and doesn't require filesystem locks.

**Stale-lock recovery:** A crashed process leaves a run `in_progress` forever, permanently blocking that `(org_id, workflow_name)`. Add a staleness threshold (default 4 hours): if the `in_progress` run's `started_at` is older than threshold, allow the new run to proceed (auto-fail the stale run first). Also add `sable-platform workflow unlock <run_id>` for manual recovery. Integrate with existing `_check_stuck_runs()` alert.

**Tests:**
1. Two concurrent runs on the same org → second raises `SableError`
2. Run on org A does not block run on org B
3. Failed/completed run does not block new run
4. Stale `in_progress` run older than threshold does not block new run
5. `workflow unlock` transitions stuck run to `failed` and unblocks

### SP-AUTH: Operator identity — HIGH [M]

**Tier 2 per AGENTS.md** — without this, `audit_log.actor` (a shipped feature) produces meaningless data. Cannot trace who did what in a production incident.

**Current:** No operator identity. Any process with `SABLE_DB_PATH` access can run any workflow on any org. Audit log `actor` field is unused.

**Fix (phase 1):** `SABLE_OPERATOR_ID` env var. CLI reads it, stamps on `audit_log.actor`, `workflow_runs.operator_id` (new column, migration 024). Phase 2 (deferred): role-based restrictions for multi-user.

**Tests:**
1. `SABLE_OPERATOR_ID` set → appears in audit log entries
2. `SABLE_OPERATOR_ID` unset → audit log actor is `"unknown"` (not null)
3. `workflow_runs.operator_id` stamped on new runs

### SP-IDX: Compound index on entity_tags [S]

**Current:** `entity_tags(tag, is_current)` has no compound index. `list_entities_by_tag()` (alert evaluator, dashboard, SableWeb) does full table scan as tag count grows.

**Fix:** Migration 024 (batched with SP-AUTH): `CREATE INDEX IF NOT EXISTS idx_entity_tags_tag_current ON entity_tags(tag, is_current)`.

### SP-OBS: Observability foundation — HIGH (Phase 1) / MEDIUM (Phases 2-3) [M]

**Tier 2 per AGENTS.md** — workflow partial failures leave steps stuck; without logging, operators cannot distinguish healthy silence from silent breakage.

**Current:** No metrics, no tracing, no health endpoint beyond CLI. In production, "nothing happened" and "everything broke silently" look identical.

**Fix (phased):**
1. **Phase 1 (HIGH):** Replace any `print()` with Python `logging`. Structured JSON formatter for production. Ship with: adapter subprocess calls, workflow step transitions, alert evaluation results, migration runs.
2. **Phase 2 (MEDIUM):** `/health` programmatic endpoint (complements SP-4) reporting: DB reachable, migration version, last workflow per org, alert evaluator last run.
3. **Phase 3 (MEDIUM):** Prometheus-compatible metrics (workflow step latency, alert fire rate, adapter success/failure counts).

Phase 1 is self-contained and should land before SP-DEPLOY.

### SP-DEPLOY: Deployment infrastructure — HIGH [M]

**Prerequisite for SP-1 Docker image step.**

**Current:** No Dockerfile, no systemd, no compose. `sable-platform` runs as a CLI on a dev laptop.

**Fix:**
1. Dockerfile: Python image + `pip install -e .`
2. docker-compose.yaml: mounts `sable.db`, env vars, optional cron container for `alerts evaluate`
3. Wire into SP-1 CI: build Docker image on tag, push to registry

### SP-1: CI/CD pipeline [S]

**File:** New `.github/workflows/ci.yml`

**Current:** 764 tests, all passing — but only run manually via `python3 -m pytest tests/ -q`. No automated checks on PR.

**Change:** GitHub Actions on PR and push to main: `pip install -e ".[dev]"` → `ruff check .` → `mypy sable_platform` → `pytest tests/ -q`. Cache pip deps. Fail on any step.

**Why:** Every Python repo in the suite (764 + 1132 + 163 + 921 + 219 = 3099 tests) runs manually. One broken merge and no one knows until someone runs tests by hand.

### SP-WEBHOOK: Async webhook dispatch [S]

**Current:** `_dispatch_webhooks()` makes HTTP calls inline during `evaluate_alerts()`. A slow webhook endpoint blocks the entire alert evaluation loop.

**Fix:** `threading.Thread(target=_dispatch_webhooks, daemon=True)` — fire and forget. Sufficient at current scale.

**Tests:**
1. Slow webhook does not block `evaluate_alerts()` return
2. Webhook failure in background thread does not crash the alert evaluator
3. `mark_delivery_failed()` is still called on background thread failure

### SP-RETENTION: Data retention policies [S]

**Current:** No purge logic. `alerts`, `audit_log`, `cost_events`, `workflow_events` grow unbounded.

**Fix:** Add `sable-platform gc --retention-days N` (default 90). Purges resolved alerts, workflow events, cost events (keeps monthly rollups). Audit log: NEVER auto-purge. Wire into cron.

**Tests:**
1. `gc --retention-days 90` purges events older than 90 days
2. Audit log is never purged regardless of retention flag
3. Monthly cost rollups are preserved
4. `gc` on empty DB is a no-op (not an error)

### SP-2: Document entity status column contract [S]

**File:** New section in `docs/SCHEMA_CONTRACTS.md` or `docs/CROSS_REPO_INTEGRATION.md`

**Current:** `entities.status` has values `active`, `archived`, `candidate` (found in production sable.db). SableWeb added `WHERE status != 'archived'` to filter entity queries, but this contract is undocumented. If SablePlatform adds a new status value (e.g., `merged`, `inactive`), SableWeb's filter may silently include or exclude the wrong entities.

**Change:** Document:
- Valid `entities.status` values and what each means operationally
- Which statuses are "active" for display purposes (SableWeb uses `!= 'archived'`)
- When and how status transitions happen (which code paths set `archived`)
- Commitment: new status values require a note in this doc + SableWeb TODO item

### SP-3: Export JSON Schema from Pydantic models [M]

**Files:** New `sable_platform/contracts/export.py` or CLI command `sable-platform contracts export`

**Current:** SablePlatform has canonical Pydantic contracts for all cross-suite data objects. SableWeb has TypeScript types that were manually aligned. There's no automated way to verify alignment or generate matching validators.

**Change:** Script/command that calls `.model_json_schema()` on key models and writes JSON Schema files to `docs/schemas/`:
- `RunSummaryBlob` (the `run_summary_json` shape from migration 021)
- `ProspectScore` (from `db/prospects.py`)
- Entity-related shapes (`Entity`, `EntityHandle`, `EntityTag`)
- `DiagnosticRun` (the diagnostic_runs row shape)

SableWeb can use these to generate or validate Zod schemas (SableWeb item B-5). Run on every release or migration change.

**Consumer:** SableWeb B-5 (Zod runtime validation).

### SP-4: Health check query [S]

**File:** Add to `sable_platform/db/connection.py` or new `sable_platform/db/health.py`

**Current:** `sable-platform inspect health` exists but its output shape is CLI-oriented. SableWeb needs a programmatic health check.

**Change:** Add `check_db_health() -> dict` returning `{ "ok": bool, "migration_version": int, "org_count": int, "latest_diagnostic_run": str|null }`. SableWeb's `/api/health` endpoint (SableWeb B-6) can call this via direct sable.db query (shared file), but having the canonical check in SablePlatform means the CLI and any future service can also use it.

---

## SableTracking Integration Improvements (from 2026-04-04 production readiness audit)

SableTracking's platform_sync is operational but has integration gaps. These items require SablePlatform-side changes or coordination.

### TRACK-1: Metadata schema contract (P7-1 in SableTracking)

SableTracking writes 17 fields to `content_items.metadata_json` as an unversioned JSON blob. Slopper reads it via `meta.get("source_tool") == "sable_tracking"` but has no schema validation. If fields are added or renamed, consumers break silently.

**SablePlatform action:** Create `sable_platform/contracts/tracking.py` with a `TrackingMetadata(BaseModel)` contract: `schema_version: int`, all 17 fields typed. Adding a field requires bumping `schema_version`. Slopper logs a warning (not error) for unknown versions.

**Import boundary note:** SableTracking and Slopper both `pip install -e` SablePlatform already (for `sable_platform.db.*`). This contract follows the same pattern — direct Python import, not subprocess boundary crossing. The Pydantic model serves as a shared schema definition, not a runtime coupling. If direct import is undesirable, SP-3 (JSON Schema export) provides an alternative: export the schema, consumers validate against it independently.

**Current 17 fields:** source_tool, url, canonical_author_handle, quality_score, audience_annotation, timing_annotation, grok_status, engagement_score, lexicon_adoption, emotional_valence, subsquad_signal, format_type, intent_type, topic_tags, review_status, outcome_type, is_reusable_template.

### TRACK-2: Outcomes table population

SableTracking captures `outcome_type` and `outcome_description` via `/outcome` command in Sheets, but never writes to the `outcomes` table in sable.db. SableTracking TODO P7-2 will add this to `sync_to_platform()`.

**SablePlatform action:** Verify `outcomes` table schema accepts: `org_id`, `entity_id` (nullable — not all content has a linked entity), `outcome_type` (text), `description` (text), `source` ("sable_tracking"), `content_item_id` (references content_items.item_id). Add helpers to `db/outcomes.py` if not present.

### TRACK-3: Sync error → actions workflow

SableTracking's P-INT-7 accumulates per-entity sync errors. These should create `actions` entries in sable.db so operators see them in SableWeb. SableTracking TODO P7-3 will implement this.

**SablePlatform action:** Verify `register_action()` or equivalent exists and supports: `org_id`, `action_type="sync_error"`, `entity_id` (nullable), `description` (error message), `source="sable_tracking"`. If the actions API is designed for a different use case, document what SableTracking should call instead.

### TRACK-4: SableTracking sync scheduler coordination

SableTracking TODO P4-5 adds an in-process twice-weekly sync scheduler (Mon/Thu 06:00 UTC). This supersedes P-INT-3 (SablePlatform cron preset). The `twice-weekly` cron preset already exists in SablePlatform. SableTracking will use its own scheduler rather than the SablePlatform cron system since tracking sync runs inside the bot process (needs access to sheets_clients).

**No SablePlatform action required** — informational only. The `stale_tracking` alert in `alert_checks.py` should continue to fire if sync hasn't run in 14 days (existing behavior is correct as a safety net).
