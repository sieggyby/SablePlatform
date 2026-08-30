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
UPDATE sqlite_master
   SET sql = replace(sql, 'datetime(''now'')', 'strftime(''%Y-%m-%dT%H:%M:%SZ'',''now'')')
 WHERE type = 'table'
   AND sql LIKE '%datetime(''now'')%';
PRAGMA writable_schema=RESET;

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
