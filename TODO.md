# SablePlatform — Roadmap

Future work only. For completed items, see [AUDIT_HISTORY.md](AUDIT_HISTORY.md).

See CLAUDE.md for project architecture, key files, and working conventions.

---

## Feature: Entity Interaction Edge Table — remaining integration work

**Data layer complete:** Migration 014, `db/interactions.py`, `inspect interactions` CLI command — all landed. See CLAUDE.md § Entity Interaction Edges.

**Remaining:**
- **Cult Grader reply pair extraction:** Stage 4 (metric_computation) must extract individual reply pairs into `computed_metrics.json`. See `Sable_Cult_Grader/TODO.md`. Until this ships, `entity_interactions` has no data source.
- **sync_after_run() call site:** Once Cult Grader emits `reply_pairs`, add a call to `sync_interaction_edges()` inside the platform sync path (likely `prospect_diagnostic_sync` workflow or tracking adapter) when the key is present in computed metrics.
- **SableWeb relationship web:** Graph rendering lives in SableWeb. See `SableWeb/docs/TODO_product_review.md` — Session 4 Addendum.

---

## Feature: Churn Prediction — remaining integration work

**Data layer + alerting complete:** Migration 015, `db/decay.py`, `_check_member_decay()` alert, `inspect decay` CLI command — all landed. See CLAUDE.md § Entity Decay Scores.

**Remaining:**
- **Cult Grader decay scoring:** DECAY-0 through DECAY-7 in `Sable_Cult_Grader/TODO.md` must ship before this table has data.
- **sync call site:** Once Cult Grader emits `decay_scores` in `computed_metrics.json`, add a call to `sync_decay_scores()` in the same sync path as `sync_interaction_edges()`.
- **Slopper intervention playbook:** CHURN-1 and CHURN-2 in `Sable_Slopper/TODO.md` generate re-engagement strategies from at-risk member data.
- **SableWeb decay dashboard:** Visualization of at-risk members lives in SableWeb.
