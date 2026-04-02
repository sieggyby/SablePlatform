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

## Feature: Churn Prediction & Intervention Engine — Platform data layer + alerting

**Depends on:** Cult Grader churn prediction feature (decay scoring). Platform receives and stores scores — it does NOT compute them.

**New migration: decay scores table**
- New table `entity_decay_scores` (or columns on `entities` — TBD during implementation). Schema: `org_id`, `entity_id`, `decay_score` (float 0–1), `risk_tier` (text: low/medium/high/critical), `scored_at` (ISO timestamp), `run_date`, `factors_json` (nullable — serialized breakdown from Cult Grader, e.g. activity drop-off, interaction narrowing, sentiment shift).
- Unique constraint on `(org_id, entity_id)` — latest score wins, but preserve `scored_at` history if we go with a log table. Decide during implementation whether to keep a single-row-per-entity upsert (like `entity_interactions`) or an append-only log with a view for latest. Leaning upsert for simplicity.

**Sync pathway**
- `sync_decay_scores(conn, org_id, scores, run_date)` in `db/decay.py`. Idempotent upsert from Cult Grader diagnostic output. Same pattern as `sync_interaction_edges()`.
- Call site: wherever platform ingests Cult Grader computed metrics (same sync path that will call `sync_interaction_edges()`). Key in `computed_metrics.json` TBD — likely `decay_scores`.
- Each score record: `{"handle": str, "decay_score": float, "risk_tier": str, "factors": dict | None}`.

**New alert check: `_check_member_decay()`**
- Added to `alert_checks.py`. Follows existing pattern: receives `conn` + `org_id`, queries `entity_decay_scores` for high/critical tier members.
- Two severity levels:
  - `warning` — decay_score crosses high threshold (e.g. ≥ 0.6).
  - `critical` — decay_score crosses critical threshold (e.g. ≥ 0.8) AND entity has a structurally important tag (e.g. `cultist`, `voice`, `mvl`).
- `dedup_key`: `"member_decay:{entity_id}"` — follows convention in CLAUDE.md.
- Register in `evaluate_alerts()` in `alert_evaluator.py`.
- Tests: fire case + cooldown suppression case (per CLAUDE.md convention).
- Thresholds should be configurable via `orgs.config_json` (e.g. `decay_warning_threshold`, `decay_critical_threshold`). Sensible defaults baked in.

**CLI: `sable-platform inspect decay`**
- `sable-platform inspect decay ORG [--min-score N] [--tier critical|high|medium|low] [--json]`
- Default: show all entities with decay_score ≥ 0.5, sorted descending.
- Columns: handle, decay_score, risk_tier, scored_at, top factors (abbreviated).
- `--json` emits full records including `factors_json`.

**Implementation order**
1. Migration + `db/decay.py` (sync function, query helpers)
2. `_check_member_decay()` + alert evaluator registration + tests
3. `inspect decay` CLI command + tests
4. Wire sync call site once Cult Grader emits `decay_scores` in computed metrics

**Out of scope for Platform**
- Decay score computation logic — lives in Cult Grader.
- Intervention playbook generation — lives in Slopper.
- UI/dashboard rendering — lives in SableWeb.
