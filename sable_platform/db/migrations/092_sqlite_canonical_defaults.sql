-- 092_sqlite_canonical_defaults.sql
--
-- DEFECTS_FOUND item 10. Migration 090 made every WRITER produce one canonical spelling,
-- and left one class of writer behind on this dialect: the SQLite column DEFAULT.
--
-- `ensure_schema` does NOT build a SQLite database from `schema.py`. It replays the SQL
-- files in `_MIGRATIONS`, so a database created today still carries the DDL the early files
-- wrote. 48 columns across 42 tables therefore defaulted to `datetime('now')`, which
-- renders `2026-08-30 02:10:23`: a SPACE separator and no `Z`. Measured on a fresh
-- version-91 database, an INSERT that omits `orgs.created_at` stored exactly that.
--
-- That is the original defect. A column holding both `...T12:00:00Z` from Python and
-- `... 12:00:00` from its own default compares, aggregates and orders by the separator.
--
-- SQLite cannot ALTER a column default. The two ways to change one are a full table rebuild
-- or a rewrite of the stored DDL text. This file rewrites the text, because a rebuild of 42
-- tables must also reproduce every index, foreign key and constraint, and getting one of
-- those wrong is a worse outcome than the defect.
--
-- The rewrite is a literal swap and it was checked before it was written: all 48
-- occurrences of `datetime('now')` sit in a DEFAULT clause, and they appear only in table
-- objects. No index, trigger or view carries the string, and no CHECK constraint does.
--
-- The 116 timestamp defaults that later migrations already wrote canonically are untouched,
-- because they do not contain the old expression.
PRAGMA writable_schema=ON;
-- Scoped to the 42 tables by NAME, not to "any table whose DDL contains the string". The
-- safety check above was run against this schema, and a name list is what makes the code
-- enforce it. Without the list, a table added later carrying datetime('now') inside a CHECK
-- constraint or a quoted literal would be rewritten too, and integrity_check would still
-- return ok, because the rewritten DDL stays valid.
UPDATE sqlite_master
   SET sql = replace(sql, 'datetime(''now'')', 'strftime(''%Y-%m-%dT%H:%M:%SZ'',''now'')')
 WHERE type = 'table'
   AND sql LIKE '%datetime(''now'')%'
   AND name IN (
       'actions', 'alert_configs', 'alerts', 'api_tokens', 'artifacts', 'audit_log',
       'community_conversation_flags', 'content_items', 'cost_events', 'diagnostic_deltas',
       'diagnostic_runs', 'entities', 'entity_centrality_scores', 'entity_decay_scores',
       'entity_handles', 'entity_notes', 'entity_tag_history', 'entity_tags',
       'entity_watchlist', 'jobs', 'kol_candidates', 'kol_create_audit', 'kol_enrichment',
       'kol_extract_runs', 'kol_follow_edges', 'kol_handle_resolution_conflicts',
       'kol_operator_relationships', 'merge_candidates', 'merge_events',
       'metric_snapshots', 'orgs', 'outcomes', 'platform_meta', 'playbook_outcomes',
       'playbook_targets', 'project_profiles_external', 'prospect_scores', 'sync_runs',
       'watchlist_snapshots', 'webhook_subscriptions', 'workflow_events', 'workflow_runs'
   );
PRAGMA writable_schema=RESET;
-- A direct sqlite_master UPDATE does NOT bump `PRAGMA schema_version`, so a connection that
-- was already open keeps its cached schema and keeps writing the OLD default. Measured on a
-- file database: a reader opened before the rewrite still stored '2026-08-30 02:23:52'.
--
-- `RESET` reloads this connection only. These two statements are real DDL, so SQLite bumps
-- schema_version itself and every other connection reparses. A literal `PRAGMA
-- schema_version = N` cannot be used here, because the new value has to be computed and this
-- file is static SQL.
-- NO `IF NOT EXISTS` and NO `IF EXISTS`, deliberately. With them, a pre-existing table of
-- this name would skip the create and then be DROPPED, taking its data with it. Without
-- them a collision raises, ensure_schema propagates it, the transaction rolls back, and the
-- database stays at version 91. A loud failure is right for a name nothing should own.
CREATE TABLE _sable_092_schema_touch (x INTEGER);
DROP TABLE _sable_092_schema_touch;

-- The DDL rewrite fixes what future inserts write. These 48 statements fix what the stale
-- defaults already wrote. Migration 090 canonicalized the values present when it ran, so
-- the rows this catches are the ones a stale default inserted between 090 and 092.
--
-- Same expression migration 090 used, and the same guard: convert only what SQLite can
-- parse, and only when the stored value differs from its canonical form.
UPDATE "actions" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "alert_configs" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "alerts" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "api_tokens" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "artifacts" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "audit_log" SET "timestamp" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("timestamp"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("timestamp"), '')) IS NOT NULL
    AND "timestamp" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("timestamp"), ''));
UPDATE "community_conversation_flags" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "content_items" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "cost_events" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "diagnostic_deltas" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "diagnostic_runs" SET "started_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), '')) IS NOT NULL
    AND "started_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''));
UPDATE "entities" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "entities" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "entity_centrality_scores" SET "scored_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("scored_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("scored_at"), '')) IS NOT NULL
    AND "scored_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("scored_at"), ''));
UPDATE "entity_decay_scores" SET "scored_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("scored_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("scored_at"), '')) IS NOT NULL
    AND "scored_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("scored_at"), ''));
UPDATE "entity_handles" SET "added_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("added_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("added_at"), '')) IS NOT NULL
    AND "added_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("added_at"), ''));
UPDATE "entity_notes" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "entity_tag_history" SET "effective_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("effective_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("effective_at"), '')) IS NOT NULL
    AND "effective_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("effective_at"), ''));
UPDATE "entity_tags" SET "added_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("added_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("added_at"), '')) IS NOT NULL
    AND "added_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("added_at"), ''));
UPDATE "entity_watchlist" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "jobs" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "jobs" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "kol_candidates" SET "first_seen_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("first_seen_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("first_seen_at"), '')) IS NOT NULL
    AND "first_seen_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("first_seen_at"), ''));
UPDATE "kol_candidates" SET "last_seen_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen_at"), '')) IS NOT NULL
    AND "last_seen_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen_at"), ''));
UPDATE "kol_create_audit" SET "at_utc" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("at_utc"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("at_utc"), '')) IS NOT NULL
    AND "at_utc" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("at_utc"), ''));
UPDATE "kol_enrichment" SET "fetched_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("fetched_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("fetched_at"), '')) IS NOT NULL
    AND "fetched_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("fetched_at"), ''));
UPDATE "kol_extract_runs" SET "started_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), '')) IS NOT NULL
    AND "started_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''));
UPDATE "kol_follow_edges" SET "fetched_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("fetched_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("fetched_at"), '')) IS NOT NULL
    AND "fetched_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("fetched_at"), ''));
UPDATE "kol_handle_resolution_conflicts" SET "detected_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("detected_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("detected_at"), '')) IS NOT NULL
    AND "detected_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("detected_at"), ''));
UPDATE "kol_operator_relationships" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "merge_candidates" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "merge_candidates" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "merge_events" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "metric_snapshots" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "orgs" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "orgs" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "outcomes" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "platform_meta" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "playbook_outcomes" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "playbook_targets" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "project_profiles_external" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "project_profiles_external" SET "last_used_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_used_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_used_at"), '')) IS NOT NULL
    AND "last_used_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_used_at"), ''));
UPDATE "prospect_scores" SET "scored_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("scored_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("scored_at"), '')) IS NOT NULL
    AND "scored_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("scored_at"), ''));
UPDATE "sync_runs" SET "started_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), '')) IS NOT NULL
    AND "started_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''));
UPDATE "watchlist_snapshots" SET "snapshot_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("snapshot_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("snapshot_at"), '')) IS NOT NULL
    AND "snapshot_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("snapshot_at"), ''));
UPDATE "webhook_subscriptions" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "workflow_events" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "workflow_runs" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));

UPDATE schema_version SET version = 92 WHERE version < 92;
