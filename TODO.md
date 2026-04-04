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

## Feature: Entity Interaction Edge Table — remaining sync wiring

**Data layer complete:** Migration 014, `db/interactions.py`, `inspect interactions` CLI command — all landed. See CLAUDE.md § Entity Interaction Edges.

**Upstream complete (2026-04-02):** Cult Grader DECAY-0 shipped. Stage 4 now extracts reply pairs into `TweetMetrics.reply_pairs` in `computed_metrics.json`.

**Remaining:**
- **sync call site:** Add a call to `sync_interaction_edges()` inside `platform_sync.py` (Cult Grader repo) when `reply_pairs` is present in computed metrics. The data is available — it just isn't wired to the sync path yet.
- **SableWeb relationship web:** Graph rendering lives in SableWeb. See `SableWeb/docs/TODO_product_review.md` — Session 4 Addendum.

---

## Feature: Churn Prediction — remaining sync wiring

**Data layer + alerting complete:** Migration 015, `db/decay.py`, `_check_member_decay()` alert, `inspect decay` CLI command — all landed. See CLAUDE.md § Entity Decay Scores.

**Upstream complete (2026-04-03):** Cult Grader DECAY-0 through DECAY-7 all shipped. `member_decay` dict (per-member decay scores, risk tiers, pattern classifications) is merged into `diagnostic.json` as a non-fatal post-pipeline step. Slopper CHURN-1 and CHURN-2 (intervention playbook generation) also shipped.

**Remaining:**
- **sync call site:** Add a call to `sync_decay_scores()` in `platform_sync.py` (Cult Grader repo) when `member_decay` is present in `diagnostic.json`. The data is available — it just isn't wired to the sync path yet. Decay scores live in `diagnostic.member_decay.members[].weighted_decay_score` and `risk_level`.
- **SableWeb decay dashboard:** Visualization of at-risk members lives in SableWeb.

---

## Feature: Network Centrality — schema mismatch resolution

**Data layer complete:** Migration 016, `db/centrality.py`, `_check_bridge_decay()` alert, `inspect centrality` CLI command — all landed.

**Upstream partial (2026-04-02):** Cult Grader DECAY-2 (`analysis/interaction_graph.py`) computes `in_centrality` and `out_centrality` (degree-based, BFS connected components) per member. However, it does NOT compute `betweenness_centrality` or `eigenvector_centrality` — only degree centrality. Either:
- (a) Extend Cult Grader's `interaction_graph.py` to compute betweenness/eigenvector, or
- (b) Simplify this migration to use `degree_centrality` (combined in/out) only and drop betweenness/eigenvector columns.

Option (b) is recommended — betweenness/eigenvector are expensive on large graphs and the current degree + cluster_id metrics already identify bridge nodes adequately.

**Remaining:**
- Resolve schema alignment (option a or b above)
- Add `sync_centrality_scores()` call in `platform_sync.py` (Cult Grader repo) alongside interaction edges and decay scores

---

## Feature: Lead Identifier → sable.db Prospect Score Sync — remaining sync wiring

**Data layer complete:** Migration 020, `db/prospects.py`, `inspect prospects` CLI command — all landed. See CLAUDE.md § Prospect Scoring.

**Remaining:**
- **Lead Identifier side:** Add `platform_sync.py:sync_scores_to_platform()` function that calls `sync_prospect_scores()`. See `Sable_Community_Lead_Identifier/TODO.md § Platform Sync`.
- **Consumer:** SableWeb `data-service.ts:assembleProspects()` (see `SableWeb/TODO.md § Backend Implementation Plan`).
