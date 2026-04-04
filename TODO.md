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

## Feature: Lead Identifier → sable.db Prospect Score Sync — remaining sync wiring

**Data layer complete:** Migration 020, `db/prospects.py`, `inspect prospects` CLI command — all landed. See CLAUDE.md § Prospect Scoring.

**Remaining:**
- **Lead Identifier side:** Add `platform_sync.py:sync_scores_to_platform()` function that calls `sync_prospect_scores()`. See `Sable_Community_Lead_Identifier/TODO.md § Platform Sync`.
- **Consumer:** SableWeb `data-service.ts:assembleProspects()` (see `SableWeb/TODO.md § Backend Implementation Plan`).
