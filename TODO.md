# SablePlatform — Roadmap

Future work only. For completed items, see [AUDIT_HISTORY.md](AUDIT_HISTORY.md).

See CLAUDE.md for project architecture, key files, and working conventions.

---

## Feature: Run Summary JSON Blob for SableWeb (F-BLOB)

**SablePlatform side complete (2026-04-03):** Migration 021 adds `run_summary_json TEXT` column to `diagnostic_runs`.

**Remaining (Cult Grader side):**
- Add `_build_run_summary()` to `platform_sync.py` — assembles versioned JSON blob (grades, scores, narratives, lists, classification, meta)
- Modify `_upsert_diagnostic_run()` to pass `run_summary_json`
- Size cap: 50KB
- See `Sable_Cult_Grader/TODO.md § F-BLOB` for full spec

---

## Feature: Playbook Outcome Tagging Tables (F-PBTAG)

**SablePlatform side complete (2026-04-03):** Migration 022 adds `playbook_targets` and `playbook_outcomes` tables. DB helpers in `db/playbook.py`: `upsert_playbook_targets()`, `get_latest_playbook_targets()`, `list_playbook_targets()`, `record_playbook_outcomes()`, `get_latest_playbook_outcomes()`, `list_playbook_outcomes()`.

**Remaining (Cult Grader side):**
- `compute_playbook_targets()` extracts structured metric targets from playbook input
- `measure_playbook_outcomes()` compares prior targets against current metrics
- Non-fatal post-step in runner.py Stage 8
- See `Sable_Cult_Grader/TODO.md § F-PBTAG` for full spec

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

## Feature: Network Centrality — remaining sync wiring

**Schema aligned (2026-04-03):** Migration 023 adds `in_centrality`/`out_centrality` columns matching Cult Grader output. `degree_centrality` computed as average of in+out. Bridge decay alert uses `degree_centrality`. Betweenness/eigenvector columns retained (legacy, unused — SQLite can't drop columns).

**Remaining (Cult Grader side):**
- Add `sync_centrality_scores()` call in `platform_sync.py` alongside interaction edges and decay scores. Pass `in_centrality`/`out_centrality` from `interaction_graph.build_interaction_graph()` output.

---

## Feature: Lead Identifier → sable.db Prospect Score Sync — remaining sync wiring

**Data layer complete:** Migration 020, `db/prospects.py`, `inspect prospects` CLI command — all landed. See CLAUDE.md § Prospect Scoring.

**Remaining:**
- **Lead Identifier side:** Add `platform_sync.py:sync_scores_to_platform()` function that calls `sync_prospect_scores()`. See `Sable_Community_Lead_Identifier/TODO.md § Platform Sync`.
- **Consumer:** SableWeb `data-service.ts:assembleProspects()` (see `SableWeb/TODO.md § Backend Implementation Plan`).
