# SablePlatform — Roadmap

Future work only. For completed items, see [AUDIT_HISTORY.md](AUDIT_HISTORY.md).

See CLAUDE.md for project architecture, key files, and working conventions.

---

## Feature: Lead Identifier → sable.db Prospect Score Sync

**Status:** NOT STARTED
**Why:** SableWeb backend needs prospect scores in sable.db to replace hardcoded `src/data/prospects.ts`. Lead Identifier currently writes only to local files (`output/sable_leads_*.json`, `run_history.jsonl`) — no sable.db integration exists.
**Depends on:** Lead Identifier adding a `sync_scores_to_platform()` function (see `Sable_Community_Lead_Identifier/TODO.md § Platform Sync`)
**Consumer:** SableWeb `data-service.ts:assembleProspects()` (see `SableWeb/TODO.md § Backend Implementation Plan`)

### Migration 020 — `020_prospect_scores.sql`

```sql
CREATE TABLE IF NOT EXISTS prospect_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id          TEXT NOT NULL,
    run_date        TEXT NOT NULL,
    composite_score REAL NOT NULL,
    tier            TEXT NOT NULL,         -- A/B/C/D
    stage           TEXT,                  -- lead/qualified/proposal/engaged/dormant
    dimensions_json TEXT NOT NULL,         -- JSON: {community_health, language_signal, growth_trajectory, engagement_quality, sable_fit}
    rationale_json  TEXT,                  -- JSON: {community_gap, tge_proximity, contact_recommendations, signal_gaps}
    enrichment_json TEXT,                  -- JSON: {sector, follower_count, display_name, confidence, pass_level}
    next_action     TEXT,
    scored_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(org_id, run_date)
);

CREATE INDEX IF NOT EXISTS idx_prospect_scores_org ON prospect_scores(org_id);
CREATE INDEX IF NOT EXISTS idx_prospect_scores_date ON prospect_scores(run_date);

UPDATE schema_version SET version = 20 WHERE version < 20;
```

### DB helper — `sable_platform/db/prospects.py`

Follow the pattern in `db/decay.py`:

- `sync_prospect_scores(conn, scores: list[dict], run_date: str) -> int`
  - Each score dict maps from Lead Identifier output JSON (see `Sable_Community_Lead_Identifier/models.py:ScoredProject`)
  - `org_id` = project slug from Lead Identifier (e.g., `"psy_protocol"`, `"zoth"`)
  - Upsert on `(org_id, run_date)` — one score per project per run date
  - Store `dimensions` as JSON: `{"community_health": 0.50, "language_signal": 0.27, ...}`
  - Store `rationale` fields as JSON
  - Return count of upserted rows

- `list_prospect_scores(conn, *, min_score: float = 0.0, tier: str | None = None, run_date: str | None = None, limit: int = 50) -> list[Row]`
  - Default: latest `run_date` only (most recent run)
  - Order by `composite_score DESC`

- `get_prospect_summary(conn, run_date: str | None = None) -> dict`
  - Return `{"total_scored": int, "by_tier": {"A": int, "B": int, ...}, "run_date": str}`

### CLI — `inspect prospects` in `inspect_cmds.py`

- `sable-platform inspect prospects [--min-score N] [--tier A|B|C|D] [--run-date YYYY-MM-DD] [--limit N] [--json]`
- Table columns: `ORG_ID`, `SCORE`, `TIER`, `STAGE`, `SECTOR`, `RUN_DATE`
- Follow exact pattern of `inspect decay` command

### Registration

- Add `("020_prospect_scores.sql", 20)` to `_MIGRATIONS` in `connection.py`
- Note: migrations 017–019 already exist (webhooks, audit_log, diagnostic_deltas). If another migration has claimed 020, use the next available number.

### Tests — `tests/db/test_prospects.py`

Follow `tests/db/test_decay.py` pattern:
1. `test_prospect_scores_table_columns` — verify all columns exist
2. `test_sync_inserts_new_scores` — insert 3 scores, verify count and values
3. `test_sync_upserts_existing` — same org+run_date updates score
4. `test_sync_empty_list` — returns 0
5. `test_list_sorted_by_score` — verify ordering
6. `test_list_min_score_filter` — verify filter
7. `test_list_tier_filter` — verify tier filter
8. `test_list_defaults_to_latest_run` — multiple run_dates, returns only most recent
9. `test_prospect_summary` — verify aggregates
10. `test_prospect_summary_empty` — verify zeroes

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

---

## Feature: Network Centrality Scores

Store graph centrality metrics computed by Cult Grader. Platform receives and stores — it does not compute. Builds on entity_interactions (migration 014) by quantifying structural importance of nodes in the interaction graph.

### Migration 016 — `016_entity_centrality.sql`

```sql
CREATE TABLE IF NOT EXISTS entity_centrality_scores (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id                  TEXT NOT NULL REFERENCES orgs(org_id),
    entity_id               TEXT NOT NULL,
    degree_centrality       REAL NOT NULL DEFAULT 0.0,
    betweenness_centrality  REAL NOT NULL DEFAULT 0.0,
    eigenvector_centrality  REAL NOT NULL DEFAULT 0.0,
    scored_at               TEXT NOT NULL DEFAULT (datetime('now')),
    run_date                TEXT NOT NULL,
    UNIQUE(org_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_centrality_org ON entity_centrality_scores(org_id);

UPDATE schema_version SET version = 16 WHERE version < 16;
```

### DB helper — `sable_platform/db/centrality.py`

Follow the pattern in `db/decay.py` exactly.

- `sync_centrality_scores(conn, org_id, scores: list[dict], run_date: str) -> int`
  - Each score dict: `{"handle": str, "degree": float, "betweenness": float, "eigenvector": float}`
  - Resolve handle to entity_id via `entity_handles` (same pattern as `sync_decay_scores`); normalize fallback with `handle.lower().lstrip("@")`
  - Validate org exists; raise `SableError(ORG_NOT_FOUND)` if not
  - `INSERT ... ON CONFLICT (org_id, entity_id) DO UPDATE SET` all three centrality values + `scored_at` + `run_date`
  - Return count of upserted rows

- `list_centrality_scores(conn, org_id, *, min_degree: float = 0.0, limit: int = 50) -> list[Row]`
  - Filter by `degree_centrality >= min_degree`
  - Order by `betweenness_centrality DESC` (bridge nodes first)

- `get_centrality_summary(conn, org_id) -> dict`
  - Return `{"scored_entities": int, "avg_degree": float, "avg_betweenness": float, "max_betweenness_entity": str | None}`
  - Ties for `max_betweenness_entity`: break arbitrarily (`ORDER BY betweenness_centrality DESC LIMIT 1`)
  - No range validation on centrality values — store whatever Cult Grader emits. Normalization is Cult Grader's responsibility.

### Alert check — `_check_bridge_decay` in `alert_checks.py`

Fires when a high-centrality entity also has a high decay score — a structurally critical bridge node at risk of churning.

- Query: `SELECT c.entity_id, c.betweenness_centrality, d.decay_score FROM entity_centrality_scores c JOIN entity_decay_scores d ON c.org_id = d.org_id AND c.entity_id = d.entity_id WHERE c.org_id = ? AND c.betweenness_centrality >= ? AND d.decay_score >= ?`
- Constants: `BRIDGE_CENTRALITY_THRESHOLD = 0.3`, `BRIDGE_DECAY_THRESHOLD = 0.6`
- Both thresholds configurable via `orgs.config_json` keys `bridge_centrality_threshold` and `bridge_decay_threshold`
- Severity: `critical` (bridge + decay is always critical)
- `dedup_key`: `"bridge_decay:{org_id}:{entity_id}"`
- Call `_deliver()` with same pattern as `_check_member_decay`
- Register in `evaluate_alerts()` per-org try block

### CLI — `inspect centrality` in `inspect_cmds.py`

- `sable-platform inspect centrality ORG [--min-degree N] [--limit N] [--json]`
- Table columns: `ENTITY_ID`, `DEGREE`, `BETWEENNESS`, `EIGENVECTOR`, `RUN_DATE`
- Follow exact pattern of `inspect decay` command

### Registration

- Add `("016_entity_centrality.sql", 16)` to `_MIGRATIONS` in `connection.py`

### Tests — `tests/db/test_centrality.py`

Follow the pattern in `tests/db/test_decay.py`:
1. `test_entity_centrality_scores_table_columns` — verify all columns exist
2. `test_sync_inserts_new_scores` — insert 2 scores, verify count and values
3. `test_sync_upserts_existing` — insert then update same handle, verify 1 row
4. `test_sync_resolves_handle_to_entity_id` — insert entity+handle, verify entity_id used
5. `test_sync_falls_back_to_handle` — no entity, verify normalized handle stored
6. `test_sync_rejects_unknown_org` — raises `SableError(ORG_NOT_FOUND)`
7. `test_sync_empty_list` — returns 0
8. `test_list_sorted_by_betweenness` — verify ordering
9. `test_list_min_degree_filter` — verify filter
10. `test_centrality_summary` — verify aggregates
11. `test_centrality_summary_empty` — verify zeroes

### Tests — `tests/alerts/test_bridge_decay_alert.py`

Follow the pattern in `tests/alerts/test_member_decay_alert.py`:
1. `test_bridge_decay_fires_critical` — entity with betweenness >= 0.3 and decay >= 0.6 fires critical
2. `test_low_centrality_no_alert` — high decay but low centrality = no alert
3. `test_low_decay_no_alert` — high centrality but low decay = no alert
4. `test_cooldown_suppresses_duplicate` — second call returns empty
5. `test_config_override_thresholds` — override via `orgs.config_json`
6. `test_bridge_decay_in_evaluate_alerts` — integration with `evaluate_alerts()`

### Tests — `tests/cli/test_inspect_centrality.py`

Follow pattern of `tests/cli/test_inspect_decay.py`.

### Dependency

Cult Grader must compute centrality from reply-pair data (NetworkX or equivalent) and emit `centrality_scores` in `computed_metrics.json`. Platform syncs via `sync_centrality_scores()` in the same sync path as interactions and decay.

---

## Feature: Entity Watchlist

Operator-curated list of entities to monitor with targeted change-trigger alerts. Instead of firehose alerting on all entities, operators pin specific members and get notified only when those members' state changes (decay score shift, tag change, new interactions).

### Migration 017 — `017_entity_watchlist.sql`

```sql
CREATE TABLE IF NOT EXISTS entity_watchlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id      TEXT NOT NULL REFERENCES orgs(org_id),
    entity_id   TEXT NOT NULL,
    added_by    TEXT NOT NULL,
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(org_id, entity_id)
);

CREATE TABLE IF NOT EXISTS watchlist_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id      TEXT NOT NULL REFERENCES orgs(org_id),
    entity_id   TEXT NOT NULL,
    decay_score REAL,
    tags_json   TEXT,
    interaction_count INTEGER,
    snapshot_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_watchlist_org ON entity_watchlist(org_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_snap ON watchlist_snapshots(org_id, entity_id, snapshot_at);

UPDATE schema_version SET version = 17 WHERE version < 17;
```

### DB helper — `sable_platform/db/watchlist.py`

- `add_to_watchlist(conn, org_id, entity_id, added_by, note=None) -> bool`
  - Validate org exists; raise `SableError(ORG_NOT_FOUND)` if not
  - `entity_id` is not validated against the `entities` table — operators may watch raw handles (same pattern as decay scores)
  - `INSERT OR IGNORE` — return True if inserted, False if already watched
  - Only if inserted (True): take an initial snapshot via `_take_snapshot`. Skip snapshot on False (duplicate add).

- `remove_from_watchlist(conn, org_id, entity_id) -> bool`
  - Delete row. Return True if deleted, False if not found.

- `list_watchlist(conn, org_id, *, limit: int = 50) -> list[Row]`
  - Return all watched entities for org, ordered by `created_at DESC`

- `_take_snapshot(conn, org_id, entity_id) -> None`
  - Read current `decay_score` from `entity_decay_scores WHERE org_id=? AND entity_id=?` (or None if not present)
  - Read current tags: `SELECT tag FROM entity_tags WHERE entity_id=? AND is_current=1 AND (expires_at IS NULL OR expires_at > datetime('now'))` -> JSON list (matches the canonical `_ACTIVE_PREDICATE` in `tags.py`)
  - Read interaction count: first, resolve entity_id to all known handles via `SELECT handle FROM entity_handles WHERE entity_id=?`. If handles found, query `SELECT COALESCE(SUM(count), 0) FROM entity_interactions WHERE org_id=? AND (source_handle IN (...) OR target_handle IN (...))`. If no handles found, fall back to using entity_id as a handle directly. Store the sum as `interaction_count`.
  - Insert into `watchlist_snapshots`

- `take_all_snapshots(conn, org_id) -> int`
  - For each entity in the org's watchlist, call `_take_snapshot`. Return count.

- `get_watchlist_changes(conn, org_id) -> list[dict]`
  - For each watched entity, compare the two most recent snapshots
  - Return list of `{"entity_id": str, "changes": list[str]}` where changes is human-readable:
    - `"decay_score: 0.5 -> 0.7"` (any change in decay_score)
    - `"tag added: cultist_candidate"` (tags in new snapshot not in old)
    - `"tag removed: voice"` (tags in old snapshot not in new)
    - `"interaction_count: 12 -> 18"` (any change in interaction count)
  - Parse `tags_json` as JSON list, compute set difference in both directions
  - If only one snapshot exists (just added), return `{"entity_id": ..., "changes": ["newly watched"]}`

### Alert check — `_check_watchlist_changes` in `alert_checks.py`

- First call `take_all_snapshots(conn, org_id)` to capture fresh state before comparison
- Then call `get_watchlist_changes(conn, org_id)`
- For each entity with non-empty changes (excluding "newly watched"):
  - Severity: `warning` by default; `critical` if any change involves `decay_score` increase >= 0.1
  - `dedup_key`: `"watchlist_change:{org_id}:{entity_id}"`
  - Title: `"Watched member {entity_id} changed: {changes_summary}"`
- Register in `evaluate_alerts()` per-org try block

### CLI

**`sable-platform watchlist add ORG ENTITY_ID [--note TEXT]`** — add to watchlist
**`sable-platform watchlist remove ORG ENTITY_ID`** — remove (print "Entity {entity_id} not on watchlist for {org_id}" if not found)
**`sable-platform watchlist list ORG [--json]`** — show watched entities with latest snapshot data
**`sable-platform watchlist changes ORG [--json]`** — show recent changes for watched entities
**`sable-platform watchlist snapshot ORG`** — manually trigger snapshot for all watched entities

CLI group: add a new `watchlist_cmds.py` in `sable_platform/cli/`. Register in `main.py` the same way other command groups are registered.

### Registration

- Add `("017_entity_watchlist.sql", 17)` to `_MIGRATIONS` in `connection.py`

### Tests — `tests/db/test_watchlist.py`

1. `test_watchlist_table_columns` — verify both tables have expected columns
2. `test_add_to_watchlist` — insert, verify row exists
3. `test_add_duplicate_returns_false` — second add returns False
4. `test_add_rejects_unknown_org` — raises SableError(ORG_NOT_FOUND)
5. `test_remove_from_watchlist` — insert then remove, verify gone
6. `test_remove_nonexistent_returns_false`
7. `test_list_watchlist_ordering` — add 3, verify created_at DESC
8. `test_initial_snapshot_taken_on_add` — after add, verify snapshot row exists
9. `test_take_all_snapshots` — add 2 entities, call take_all_snapshots, verify 2 new snapshot rows
10. `test_get_changes_decay_shift` — two snapshots with different decay_score, verify change reported
11. `test_get_changes_tag_added` — snapshot 1 has no tag, snapshot 2 has tag, verify reported
12. `test_get_changes_no_change` — two identical snapshots, verify empty changes
13. `test_get_changes_newly_watched` — only 1 snapshot, verify "newly watched"

### Tests — `tests/alerts/test_watchlist_alert.py`

1. `test_watchlist_change_fires_warning` — entity with decay shift, verify warning
2. `test_large_decay_shift_fires_critical` — decay increase >= 0.1, verify critical
3. `test_no_changes_no_alert` — identical snapshots, no alert
4. `test_cooldown_suppresses_duplicate`
5. `test_watchlist_in_evaluate_alerts` — integration

### Tests — `tests/cli/test_watchlist_cmds.py`

1. `test_watchlist_add_and_list` — add via CLI runner, list via CLI runner
2. `test_watchlist_remove` — add then remove
3. `test_watchlist_changes_json` — verify --json output
4. `test_watchlist_snapshot` — verify snapshot command runs

---

## Feature: Operator Dashboard

Single command answering "what needs my attention right now?" across all orgs. Collapses 6 overlapping proposals (composite health score, morning briefing, dashboard, weekly digest) into one read-only CLI aggregation with no new migration.

### CLI — `sable-platform dashboard [--org ORG] [--json]`

Add to `inspect_cmds.py` (or a new `dashboard_cmds.py` if cleaner — use judgment).

**Output for each org (sorted by urgency):**

1. **Open alerts** — count by severity (critical / warning / info) from `list_alerts(conn, org_id=oid, status="new")`
2. **Stale data** — for each sync type, days since last completed sync from `sync_runs`
3. **Stuck runs** — count of `workflow_runs` with `status='running' AND started_at < datetime('now', '-2 hours')`
4. **Upcoming actions** — count of unclaimed actions from `actions` where `status='pending'`
5. **Budget** — weekly spend and headroom from `get_weekly_spend()` and budget cap (read cap from `orgs.config_json` or default $5.00). If `budget_cap <= 0`, display `pct_used` as `N/A` and skip headroom.
6. **Decay risk** — count of entities with `decay_score >= 0.6` from `entity_decay_scores`

**Urgency sort:** Orgs with critical alerts first, then orgs with stale data, then by open alert count descending.

**Implementation detail:**
- No new migration. No new DB helper module. Pure read-only aggregation of existing queries.
- `--org ORG` filters to single org. Without it, shows all active orgs.
- `--json` outputs list of dicts, one per org.
- Human output: one block per org with header line and indented details. Use `click.echo()` and `click.style()` for severity coloring (critical=red, warning=yellow).

### Tests — `tests/cli/test_dashboard.py`

1. `test_dashboard_empty_db` — no orgs, prints "No active orgs"
2. `test_dashboard_single_org` — insert org + some data, verify output contains org_id
3. `test_dashboard_json_output` — verify `--json` returns parseable JSON list
4. `test_dashboard_org_filter` — two orgs, `--org` shows only one
5. `test_dashboard_urgency_sort` — org with critical alert sorts before org with only warnings

---

## Feature: Inspect Spend

Surface the existing `db/cost.py` data via CLI. No new migration — the `cost_events` table already exists. This feature only adds CLI wiring.

### CLI — `sable-platform inspect spend [--org ORG] [--json]`

Add to `inspect_cmds.py`.

**Per-org output:**
- `weekly_spend_usd`: from `get_weekly_spend(conn, org_id)`
- `budget_cap_usd`: from `orgs.config_json["max_ai_usd_per_org_per_week"]` or platform default ($5.00)
- `headroom_usd`: `budget_cap - weekly_spend`
- `pct_used`: `weekly_spend / budget_cap * 100`
- `total_calls_this_week`: `SELECT COUNT(*) FROM cost_events WHERE org_id=? AND created_at >= ?` (same week window as `get_weekly_spend`)

**Without `--org`:** Show all active orgs sorted by `pct_used` DESC (most-spent first).

**Implementation:**
- Import `get_weekly_spend` from `db/cost.py`
- Read budget cap from `orgs.config_json` with JSON parse and fallback to 5.0
- Human table: `ORG_ID  SPEND  CAP  HEADROOM  PCT_USED`
- `--json` outputs list of dicts

### Tests — `tests/cli/test_inspect_spend.py`

1. `test_spend_no_cost_events` — org exists but no cost data, shows $0.00
2. `test_spend_with_cost_events` — insert cost_events, verify spend shown
3. `test_spend_json` — verify `--json` parseable
4. `test_spend_all_orgs` — two orgs, both shown sorted by pct_used

---

## Feature: Preflight Gate

Machine-friendly health check that exits 0 if an org is ready for a workflow run, exits non-zero with structured diagnostics if not. Designed for cron jobs and scripted pipelines.

### CLI — `sable-platform preflight [--org ORG]`

Add to `workflow_cmds.py` (or `inspect_cmds.py` — use judgment based on where it fits).

**Checks (all must pass for exit 0):**
1. **Org exists and is active** — `SELECT status FROM orgs WHERE org_id=?`
2. **No stuck runs** — `SELECT COUNT(*) FROM workflow_runs WHERE status='running' AND started_at < datetime('now', '-2 hours')` (org-scoped if `--org`)
3. **Budget headroom** — `get_weekly_spend(conn, org_id) < budget_cap * 0.90` (fail if >= 90%)
4. **No critical alerts** — `SELECT COUNT(*) FROM alerts WHERE org_id=? AND severity='critical' AND status='new'` must be 0

**Output on failure:** One line per failed check: `FAIL: <check_name> — <detail>`. Exit code 1.

**Output on success:** `OK: <org_id> ready` (or `OK: all orgs ready` without `--org`). Exit code 0.

**Without `--org`:** Run checks for ALL active orgs. Exit 1 if ANY org fails. Print per-org results.

**Implementation:**
- Use `sys.exit(0)` / `sys.exit(1)` (or `ctx.exit()` in Click)
- Import `get_weekly_spend` from `db/cost.py`
- Keep checks simple and fast — no external calls

### Tests — `tests/cli/test_preflight.py`

Use Click's `CliRunner` (default `catch_exceptions=True`). Assert `result.exit_code == 0` or `result.exit_code == 1`.

1. `test_preflight_healthy_org` — clean org, verify exit 0 and "OK" in output
2. `test_preflight_stuck_run` — insert running workflow > 2h old, verify exit 1 and "stuck" in output
3. `test_preflight_budget_exceeded` — insert cost events at 95% cap, verify exit 1
4. `test_preflight_critical_alert` — insert critical alert, verify exit 1
5. `test_preflight_missing_org` — nonexistent org, verify exit 1
6. `test_preflight_all_orgs` — two orgs (one healthy, one failing), verify exit 1 and both mentioned
7. `test_preflight_all_orgs_healthy` — two healthy orgs, verify exit 0

---

## Feature: Operator Audit Log

Append-only audit trail for operator and system actions that mutate org or entity state. Compliance requirement: Sable must answer "who did what to our data and when?"

### Migration 018 — `018_audit_log.sql`

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    org_id      TEXT,
    entity_id   TEXT,
    detail_json TEXT,
    source      TEXT NOT NULL DEFAULT 'cli'
);

CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_log(org_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor, timestamp);

UPDATE schema_version SET version = 18 WHERE version < 18;
```

**Design notes:**
- No FK constraints on `org_id` or `entity_id` — the audit log must survive even if the referenced entity is archived/deleted.
- `actor` is a free-text operator identifier (e.g., "cli:alice", "system:evaluate_alerts").
- `action` is a verb string: `"alert_acknowledge"`, `"alert_mute"`, `"entity_merge"`, `"tag_deactivate"`, `"watchlist_add"`, `"workflow_resume"`, `"workflow_cancel"`, `"config_update"`, etc.
- `source` is `"cli"`, `"api"`, or `"system"`.

### DB helper — `sable_platform/db/audit.py`

- `log_audit(conn, actor: str, action: str, *, org_id: str | None = None, entity_id: str | None = None, detail: dict | None = None, source: str = "cli") -> int`
  - Insert row. Return the `id` of the inserted row.
  - `detail_json = json.dumps(detail)` if detail else None.
  - **Content policy:** Never store credentials, PII beyond entity handles, or raw API responses in `detail_json`. Keep detail dicts to identifiers and short reason strings.

- `list_audit_log(conn, *, org_id: str | None = None, actor: str | None = None, action: str | None = None, since: str | None = None, limit: int = 100) -> list[Row]`
  - Filter by any combination. Order by `timestamp DESC`.
  - `since` is an ISO datetime string in UTC; filter `timestamp >= since`. All audit timestamps are UTC (SQLite `datetime('now')`).

### Integration — instrument existing mutation sites

Add `log_audit()` calls to these existing functions (one-line addition each):
- `alerts.py: acknowledge_alert()` — `log_audit(conn, operator, "alert_acknowledge", org_id=..., detail={"alert_id": alert_id})`
- `alerts.py: resolve_alert()` — `log_audit(conn, "system", "alert_resolve", ...)`
- `tags.py: deactivate_tag()` — `log_audit(conn, source or "system", "tag_deactivate", entity_id=entity_id, detail={"tag": tag, "reason": reason})`
- `entities.py: archive_entity()` — `log_audit(conn, "system", "entity_archive", ...)`
- `merge.py` (entity merge function) — `log_audit(conn, "system", "entity_merge", ...)`
- Watchlist add/remove (from the watchlist feature above)

**Do NOT instrument** high-frequency write paths (sync_decay_scores, sync_interaction_edges, log_cost) — those would bloat the audit log with non-operator actions.

### CLI — `sable-platform inspect audit [--org ORG] [--actor ACTOR] [--action ACTION] [--since DATETIME] [--limit N] [--json]`

Add to `inspect_cmds.py`.

- Human table: `TIMESTAMP  ACTOR  ACTION  ORG  ENTITY  DETAIL`
- `--json` outputs list of dicts

### Registration

- Add `("018_audit_log.sql", 18)` to `_MIGRATIONS` in `connection.py`

### Tests — `tests/db/test_audit.py`

1. `test_log_audit_basic` — insert, verify row exists with correct fields
2. `test_log_audit_with_detail` — verify detail_json round-trips
3. `test_list_audit_all` — insert 3 entries, list all, verify order
4. `test_list_audit_filter_org` — filter by org_id
5. `test_list_audit_filter_actor` — filter by actor
6. `test_list_audit_filter_action` — filter by action type
7. `test_list_audit_filter_since` — filter by timestamp
8. `test_list_audit_combined_filters` — org + actor + since

### Tests — `tests/cli/test_inspect_audit.py`

1. `test_inspect_audit_empty` — no entries, prints "No audit entries"
2. `test_inspect_audit_with_entries` — insert entries, verify CLI output
3. `test_inspect_audit_json` — verify `--json` parseable

### Tests — `tests/db/test_audit_integration.py`

1. `test_acknowledge_alert_creates_audit_entry` — acknowledge an alert, verify audit row
2. `test_deactivate_tag_creates_audit_entry` — deactivate a tag, verify audit row
3. `test_watchlist_add_creates_audit_entry` — add to watchlist, verify audit row

---

## Feature: Workflow Event Webhooks

Turn SablePlatform into a composable platform by exposing workflow lifecycle and alert events over HTTP webhooks. External systems (SableWeb, Slack bots, monitoring) subscribe to events and receive signed payloads.

### Migration 019 — `019_webhooks.sql`

```sql
CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id          TEXT NOT NULL REFERENCES orgs(org_id),
    url             TEXT NOT NULL,
    event_types     TEXT NOT NULL,
    secret          TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_failure_at TEXT,
    last_failure_error TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(org_id, url)
);

UPDATE schema_version SET version = 19 WHERE version < 19;
```

**Notes:**
- `event_types` is a JSON list of strings: `["workflow.completed", "workflow.failed", "alert.created"]`
- `secret` is used for HMAC-SHA256 payload signing
- Auto-disable after 10 consecutive failures (`enabled = 0`)

### DB helper — `sable_platform/db/webhooks.py`

- `MAX_SUBSCRIPTIONS_PER_ORG = 5` — reject creation if org already has 5 enabled subscriptions (prevents `3N` second worst-case latency from synchronous dispatch)

- `create_subscription(conn, org_id, url, event_types: list[str], secret: str) -> int`
  - Validate org exists. Validate `len(secret) >= 16` (raise `SableError` if shorter). Validate URL does not start with `http://localhost`, `http://127.0.0.1`, `http://0.0.0.0`, or RFC 1918 prefixes (`http://10.`, `http://192.168.`, `http://172.16.`–`http://172.31.`) — raise `SableError` for SSRF prevention.
  - Check count of enabled subscriptions for org; raise if >= `MAX_SUBSCRIPTIONS_PER_ORG`.
  - Insert row. Return id.
  - `event_types` stored as `json.dumps(event_types)`

- `list_subscriptions(conn, org_id) -> list[Row]`
  - **Mask secret in return value:** only show last 4 characters (e.g., `"****abcd"`). The raw secret is never exposed via list or CLI.

- `delete_subscription(conn, subscription_id: int) -> bool`

- `record_failure(conn, subscription_id: int, error: str) -> None`
  - Increment `consecutive_failures`. Set `last_failure_at`, `last_failure_error`.
  - If `consecutive_failures >= 10`, set `enabled = 0`.

- `record_success(conn, subscription_id: int) -> None`
  - Reset `consecutive_failures = 0`.

### Dispatch — `sable_platform/webhooks/dispatch.py`

- `dispatch_event(conn, event_type: str, org_id: str, payload: dict) -> int`
  - Query `webhook_subscriptions WHERE org_id=? AND enabled=1`
  - For each subscription where `event_type` is in the subscription's `event_types` list (JSON parse):
    - Build JSON body: `{"event_type": event_type, "org_id": org_id, "timestamp": utcnow_iso, "payload": payload}`
    - Serialize body once with `json.dumps(body, separators=(',', ':'), sort_keys=True).encode()` — these exact bytes are both the POST body and the HMAC input
    - Compute HMAC-SHA256 of those bytes using `subscription.secret.encode()` as key
    - POST with `Content-Type: application/json` and `X-Sable-Signature: sha256=<hex_digest>` header
    - Timeout: **3 seconds** (synchronous, fire-and-forget pattern consistent with engine design)
    - On success: call `record_success(conn, sub_id)`
    - On failure: call `record_failure(conn, sub_id, str(error))`, log warning, continue (never raise)
  - Return count of successful deliveries

### Integration points

Add `dispatch_event()` calls in two existing chokepoints:

1. **`engine.py` — `emit_workflow_event()`:** After the existing event insert, call `dispatch_event(conn, f"workflow.{event_type}", org_id, {"run_id": run_id, "workflow_name": name, "step_name": step_name, ...})`. Wrap in try/except (webhook failure must never block the engine).

2. **`alert_delivery.py` — `_deliver()`:** After alert is logged, call `dispatch_event(conn, "alert.created", org_id, {"alert_type": alert_type, "severity": severity, "title": message})`. Wrap in try/except.

**Supported event types:** `workflow.started`, `workflow.completed`, `workflow.failed`, `workflow.step_completed`, `workflow.step_failed`, `alert.created`.

### CLI — `sable-platform webhooks` group

New `webhook_cmds.py`:
- `sable-platform webhooks add ORG --url URL --events EVENT1,EVENT2 --secret SECRET [--generate-secret]` (if `--generate-secret`, ignore `--secret` and output a `secrets.token_hex(32)` value; print the generated secret once since it won't be shown again)
- `sable-platform webhooks list ORG [--json]`
- `sable-platform webhooks remove ID`
- `sable-platform webhooks test ORG ID` — send a test `webhook.test` event to a specific subscription

### Registration

- Add `("019_webhooks.sql", 19)` to `_MIGRATIONS` in `connection.py`
- Create `sable_platform/webhooks/` package with `__init__.py` and `dispatch.py`
- Register `webhook_cmds` group in `cli/main.py`

### Tests — `tests/db/test_webhooks.py`

1. `test_create_subscription` — insert, verify row
2. `test_create_rejects_short_secret` — secret < 16 chars, verify SableError raised
3. `test_create_rejects_localhost_url` — url starts with http://localhost, verify SableError
4. `test_create_rejects_over_max_subscriptions` — insert MAX_SUBSCRIPTIONS_PER_ORG, verify 6th is rejected
5. `test_list_subscriptions` — insert 2, list, verify both returned
6. `test_list_subscriptions_masks_secret` — verify secret is masked (only last 4 chars visible)
7. `test_delete_subscription` — insert then delete
8. `test_record_failure_increments` — call record_failure 3x, verify count=3
9. `test_auto_disable_after_10_failures` — call record_failure 10x, verify enabled=0
10. `test_record_success_resets` — fail 5x then succeed, verify count=0

### Tests — `tests/webhooks/test_dispatch.py`

Use `unittest.mock.patch("urllib.request.urlopen")`:
1. `test_dispatch_sends_to_matching_subscription` — mock urlopen, verify called with correct URL
2. `test_dispatch_skips_non_matching_event` — subscription for "alert.created", dispatch "workflow.completed", verify not called
3. `test_dispatch_includes_hmac_signature` — verify X-Sable-Signature header present and correct
4. `test_dispatch_failure_does_not_raise` — mock urlopen to raise, verify no exception propagated
5. `test_dispatch_records_failure_on_error` — mock urlopen to raise, verify consecutive_failures incremented
6. `test_dispatch_skips_disabled_subscription` — set enabled=0, verify not called
7. `test_dispatch_returns_success_count` — 2 subscriptions, 1 fails, verify returns 1

### Tests — `tests/cli/test_webhook_cmds.py`

1. `test_webhooks_add_and_list` — add via CLI, list via CLI
2. `test_webhooks_remove` — add then remove
3. `test_webhooks_list_json` — verify `--json` parseable

---

## Version bump checklist (for implementer)

After all features are implemented:
- Update `CLAUDE.md` "Current State" section: migration count (019), test count, CLI listing, key files table, alert type list
- Update `tests/test_init.py` and `tests/test_migrations.py` version assertions to 19
- Run full suite: `python3 -m pytest tests/ -q` — all tests must pass
- Add completed feature entries to `AUDIT_HISTORY.md`
