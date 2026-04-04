# SablePlatform — Roadmap

Future work only. For completed items, see [AUDIT_HISTORY.md](AUDIT_HISTORY.md).

See CLAUDE.md for project architecture, key files, and working conventions.

---

## Feature: Run Summary JSON Blob for SableWeb (F-BLOB)

**Status:** NOT STARTED — requires coordinated work across Cult Grader and SablePlatform.
**Priority:** P1 — Unblocks all SableWeb dashboard rendering.
**Cross-repo:** `Sable_Cult_Grader/TODO.md § F-BLOB` has the full spec.

**Problem:** `platform_sync.py` writes only 7 scalar fields to `diagnostic_runs`. SableWeb cannot render rich dashboards without filesystem access to checkpoint files.

**SablePlatform side:**
- Migration 021: Add `run_summary_json TEXT` column to `diagnostic_runs`
- No new DB helper needed — the column is written by Cult Grader's `_upsert_diagnostic_run()` in the existing INSERT/UPDATE

**Cult Grader side (see their TODO for full spec):**
- Add `_build_run_summary()` to `platform_sync.py` — assembles versioned JSON blob (grades, scores, narratives, lists, classification, meta)
- Modify `_upsert_diagnostic_run()` to pass `run_summary_json`
- Size cap: 50KB

**Cost:** $0/run. Pure Python + SQLite.

---

## Feature: Playbook Outcome Tagging Tables (F-PBTAG)

**Status:** NOT STARTED — requires coordinated work across Cult Grader and SablePlatform.
**Priority:** P2 — Closes the diagnostic-playbook-outcome feedback loop.
**Cross-repo:** `Sable_Cult_Grader/TODO.md § F-PBTAG` has the full spec.

**Problem:** Playbook generates recommendations as unstructured markdown. Next diagnostic run cannot measure whether recommendations were acted on. The feedback loop is open.

**SablePlatform side:**
- Migration: Add `playbook_targets` table (`org_id, artifact_id, targets_json, created_at`)
- Migration: Add `playbook_outcomes` table (`org_id, targets_artifact_id, outcomes_json, created_at`)
- DB helpers for upsert and query

**Cult Grader side (see their TODO for full spec):**
- `compute_playbook_targets()` extracts structured metric targets from playbook input
- `measure_playbook_outcomes()` compares prior targets against current metrics
- Non-fatal post-step in runner.py Stage 8

**Cost:** $0/run for measurement. Pure Python.

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
