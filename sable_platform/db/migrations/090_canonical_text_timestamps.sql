-- 090_canonical_text_timestamps.sql
--
-- Rewrite every TEXT timestamp into one spelling: YYYY-MM-DDTHH:MM:SSZ.
--
-- Every timestamp in this schema is TEXT, which works only while one spelling is used.
-- It was not. The column DEFAULT was CURRENT_TIMESTAMP, which writes a SPACE separator,
-- while 67 Python call sites render strftime('%Y-%m-%dT%H:%M:%SZ') with a T. Space is 0x20
-- and T is 0x54, so every comparison, ORDER BY, MIN and MAX over one of these columns was
-- decided by the separator character rather than by the clock.
--
-- Second precision with no fractional part is load-bearing, not a rounding choice.
-- Lexicographic order equals chronological order only while every value is the same width.
-- '...T12:00:00.5Z' sorts BELOW '...T12:00:00Z' because . is 0x2E and Z is 0x5A.
--
-- strftime returns NULL for anything it cannot read, and that is the guard: a value that is
-- not a timestamp is left exactly as it was. TRIM first, because strftime rejects a padded
-- value outright and a padded row would otherwise keep its whitespace and go on sorting wrong.
--
-- One SQLite behaviour differs from the PostgreSQL peer and is left as it is. SQLite ROLLS OVER
-- an impossible day: '2026-02-30' becomes '2026-03-02' rather than failing. PostgreSQL rejects
-- the same value and the peer migration skips it. A stored '2026-02-30' is corrupt either way,
-- and this is the local dialect, not production. SQLite's date functions also reject a two-digit
-- offset such as '+00', which is the PostgreSQL spelling, so a row imported from PostgreSQL
-- is left alone rather than guessed at.
--
-- The DEFAULT is not altered here, and it is NOT already correct. ensure_schema replays
-- these SQL files rather than building from schema.py, so 48 columns across 42 tables kept
-- their original datetime('now') default. Migration 092 rewrites them.
--
-- The Alembic peer is b0c1d2e3f090_canonical_text_timestamps.py, which also sets the
-- PostgreSQL defaults.
UPDATE "actions" SET "claimed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("claimed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("claimed_at"), '')) IS NOT NULL
    AND "claimed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("claimed_at"), ''));
UPDATE "actions" SET "completed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), '')) IS NOT NULL
    AND "completed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''));
UPDATE "actions" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "actions" SET "skipped_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("skipped_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("skipped_at"), '')) IS NOT NULL
    AND "skipped_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("skipped_at"), ''));
UPDATE "alert_configs" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "alerts" SET "acknowledged_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("acknowledged_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("acknowledged_at"), '')) IS NOT NULL
    AND "acknowledged_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("acknowledged_at"), ''));
UPDATE "alerts" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "alerts" SET "last_delivered_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_delivered_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_delivered_at"), '')) IS NOT NULL
    AND "last_delivered_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_delivered_at"), ''));
UPDATE "alerts" SET "resolved_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("resolved_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("resolved_at"), '')) IS NOT NULL
    AND "resolved_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("resolved_at"), ''));
UPDATE "allowlist_entries" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "allowlist_entries" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "api_tokens" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "api_tokens" SET "expires_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), '')) IS NOT NULL
    AND "expires_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''));
UPDATE "api_tokens" SET "last_used_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_used_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_used_at"), '')) IS NOT NULL
    AND "last_used_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_used_at"), ''));
UPDATE "api_tokens" SET "revoked_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("revoked_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("revoked_at"), '')) IS NOT NULL
    AND "revoked_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("revoked_at"), ''));
UPDATE "artifacts" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "audit_log" SET "timestamp" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("timestamp"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("timestamp"), '')) IS NOT NULL
    AND "timestamp" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("timestamp"), ''));
UPDATE "autocm_adversarial_runs" SET "ran_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("ran_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("ran_at"), '')) IS NOT NULL
    AND "ran_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("ran_at"), ''));
UPDATE "autocm_category_state" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "autocm_clients" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "autocm_clients" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "autocm_digest_interactions" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "autocm_drafts" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "autocm_drafts" SET "resolved_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("resolved_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("resolved_at"), '')) IS NOT NULL
    AND "resolved_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("resolved_at"), ''));
UPDATE "autocm_escalations" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "autocm_escalations" SET "resolved_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("resolved_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("resolved_at"), '')) IS NOT NULL
    AND "resolved_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("resolved_at"), ''));
UPDATE "autocm_flagged_users" SET "cleared_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("cleared_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("cleared_at"), '')) IS NOT NULL
    AND "cleared_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("cleared_at"), ''));
UPDATE "autocm_flagged_users" SET "flagged_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("flagged_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("flagged_at"), '')) IS NOT NULL
    AND "flagged_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("flagged_at"), ''));
UPDATE "autocm_kb_chunks" SET "indexed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("indexed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("indexed_at"), '')) IS NOT NULL
    AND "indexed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("indexed_at"), ''));
UPDATE "autocm_kb_constants" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "autocm_kb_sources" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "autocm_kb_sources" SET "last_changed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_changed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_changed_at"), '')) IS NOT NULL
    AND "last_changed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_changed_at"), ''));
UPDATE "autocm_kb_sources" SET "last_refreshed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_refreshed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_refreshed_at"), '')) IS NOT NULL
    AND "last_refreshed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_refreshed_at"), ''));
UPDATE "autocm_personas" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "autocm_personas" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "autocm_reviews" SET "reviewed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("reviewed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("reviewed_at"), '')) IS NOT NULL
    AND "reviewed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("reviewed_at"), ''));
UPDATE "autocm_time_saved_baseline" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "autocm_time_saved_baseline" SET "engagement_start_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("engagement_start_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("engagement_start_at"), '')) IS NOT NULL
    AND "engagement_start_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("engagement_start_at"), ''));
UPDATE "autocm_time_saved_baseline" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "client_accounts" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "client_docs" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "client_intake" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "client_intake" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "community_audit_benchmark" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "community_audit_findings" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "community_audit_guilds" SET "consent_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("consent_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("consent_at"), '')) IS NOT NULL
    AND "consent_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("consent_at"), ''));
UPDATE "community_audit_guilds" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "community_audit_guilds" SET "joined_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("joined_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("joined_at"), '')) IS NOT NULL
    AND "joined_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("joined_at"), ''));
UPDATE "community_audit_guilds" SET "last_audit_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_audit_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_audit_at"), '')) IS NOT NULL
    AND "last_audit_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_audit_at"), ''));
UPDATE "community_audit_guilds" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "community_audit_identity_links" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "community_audit_leads" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "community_audit_member_activity" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "community_audit_member_scores" SET "last_active_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_active_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_active_at"), '')) IS NOT NULL
    AND "last_active_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_active_at"), ''));
UPDATE "community_audit_member_scores" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "community_audit_rate_limits" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "community_audit_reaction_ledger" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "community_audit_runs" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "community_audit_runs" SET "finished_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("finished_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("finished_at"), '')) IS NOT NULL
    AND "finished_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("finished_at"), ''));
UPDATE "community_audit_runs" SET "started_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), '')) IS NOT NULL
    AND "started_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''));
UPDATE "community_audit_security_checks" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "community_audit_settings_snapshot" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "community_audit_vocab_corpus" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "community_conversation_flags" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "community_conversation_flags" SET "delivered_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("delivered_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("delivered_at"), '')) IS NOT NULL
    AND "delivered_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("delivered_at"), ''));
UPDATE "community_conversation_flags" SET "expires_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), '')) IS NOT NULL
    AND "expires_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''));
UPDATE "content_candidates" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "content_candidates" SET "expires_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), '')) IS NOT NULL
    AND "expires_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''));
UPDATE "content_deck_decisions" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "content_deck_operator_state" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "content_duels" SET "closed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("closed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("closed_at"), '')) IS NOT NULL
    AND "closed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("closed_at"), ''));
UPDATE "content_duels" SET "opened_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("opened_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("opened_at"), '')) IS NOT NULL
    AND "opened_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("opened_at"), ''));
UPDATE "content_items" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "content_items" SET "posted_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), '')) IS NOT NULL
    AND "posted_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''));
UPDATE "content_publish_jobs" SET "claimed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("claimed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("claimed_at"), '')) IS NOT NULL
    AND "claimed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("claimed_at"), ''));
UPDATE "content_publish_jobs" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "content_publish_jobs" SET "handed_off_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("handed_off_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("handed_off_at"), '')) IS NOT NULL
    AND "handed_off_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("handed_off_at"), ''));
UPDATE "content_publish_jobs" SET "next_attempt_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("next_attempt_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("next_attempt_at"), '')) IS NOT NULL
    AND "next_attempt_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("next_attempt_at"), ''));
UPDATE "content_publish_jobs" SET "publish_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("publish_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("publish_at"), '')) IS NOT NULL
    AND "publish_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("publish_at"), ''));
UPDATE "content_publish_jobs" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "content_quality" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "cost_events" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "deck_consumed_assertions" SET "consumed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("consumed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("consumed_at"), '')) IS NOT NULL
    AND "consumed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("consumed_at"), ''));
UPDATE "diagnostic_deltas" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "diagnostic_runs" SET "completed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), '')) IS NOT NULL
    AND "completed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''));
UPDATE "diagnostic_runs" SET "started_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), '')) IS NOT NULL
    AND "started_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''));
UPDATE "discord_burn_blocklist" SET "blocked_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("blocked_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("blocked_at"), '')) IS NOT NULL
    AND "blocked_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("blocked_at"), ''));
UPDATE "discord_burn_optins" SET "opted_in_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("opted_in_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("opted_in_at"), '')) IS NOT NULL
    AND "opted_in_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("opted_in_at"), ''));
UPDATE "discord_burn_random_log" SET "roasted_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("roasted_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("roasted_at"), '')) IS NOT NULL
    AND "roasted_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("roasted_at"), ''));
UPDATE "discord_fitcheck_emoji_milestones" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "discord_fitcheck_emoji_milestones" SET "crossed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("crossed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("crossed_at"), '')) IS NOT NULL
    AND "crossed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("crossed_at"), ''));
UPDATE "discord_fitcheck_scores" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "discord_fitcheck_scores" SET "invalidated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("invalidated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("invalidated_at"), '')) IS NOT NULL
    AND "invalidated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("invalidated_at"), ''));
UPDATE "discord_fitcheck_scores" SET "posted_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), '')) IS NOT NULL
    AND "posted_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''));
UPDATE "discord_fitcheck_scores" SET "reveal_fired_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("reveal_fired_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("reveal_fired_at"), '')) IS NOT NULL
    AND "reveal_fired_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("reveal_fired_at"), ''));
UPDATE "discord_fitcheck_scores" SET "scored_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("scored_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("scored_at"), '')) IS NOT NULL
    AND "scored_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("scored_at"), ''));
UPDATE "discord_fitcheck_scores" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "discord_guild_config" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "discord_invite_snapshot" SET "captured_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("captured_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("captured_at"), '')) IS NOT NULL
    AND "captured_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("captured_at"), ''));
UPDATE "discord_invite_snapshot" SET "expires_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), '')) IS NOT NULL
    AND "expires_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''));
UPDATE "discord_member_admit" SET "decision_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("decision_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("decision_at"), '')) IS NOT NULL
    AND "decision_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("decision_at"), ''));
UPDATE "discord_member_admit" SET "joined_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("joined_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("joined_at"), '')) IS NOT NULL
    AND "joined_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("joined_at"), ''));
UPDATE "discord_message_observations" SET "captured_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("captured_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("captured_at"), '')) IS NOT NULL
    AND "captured_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("captured_at"), ''));
UPDATE "discord_message_observations" SET "posted_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), '')) IS NOT NULL
    AND "posted_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''));
UPDATE "discord_peer_roast_flags" SET "flagged_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("flagged_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("flagged_at"), '')) IS NOT NULL
    AND "flagged_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("flagged_at"), ''));
UPDATE "discord_peer_roast_tokens" SET "consumed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("consumed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("consumed_at"), '')) IS NOT NULL
    AND "consumed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("consumed_at"), ''));
UPDATE "discord_peer_roast_tokens" SET "granted_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("granted_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("granted_at"), '')) IS NOT NULL
    AND "granted_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("granted_at"), ''));
UPDATE "discord_pulse_runs" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "discord_scoring_config" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "discord_scoring_config" SET "state_changed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("state_changed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("state_changed_at"), '')) IS NOT NULL
    AND "state_changed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("state_changed_at"), ''));
UPDATE "discord_scoring_config" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "discord_state_pins" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "discord_state_pins" SET "posted_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), '')) IS NOT NULL
    AND "posted_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''));
UPDATE "discord_state_pins" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "discord_streak_events" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "discord_streak_events" SET "invalidated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("invalidated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("invalidated_at"), '')) IS NOT NULL
    AND "invalidated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("invalidated_at"), ''));
UPDATE "discord_streak_events" SET "posted_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), '')) IS NOT NULL
    AND "posted_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''));
UPDATE "discord_streak_events" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "discord_team_inviters" SET "added_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("added_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("added_at"), '')) IS NOT NULL
    AND "added_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("added_at"), ''));
UPDATE "discord_user_observations" SET "computed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("computed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("computed_at"), '')) IS NOT NULL
    AND "computed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("computed_at"), ''));
UPDATE "discord_user_vibes" SET "inferred_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("inferred_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("inferred_at"), '')) IS NOT NULL
    AND "inferred_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("inferred_at"), ''));
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
UPDATE "entity_interactions" SET "last_seen" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen"), '')) IS NOT NULL
    AND "last_seen" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen"), ''));
UPDATE "entity_notes" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "entity_tag_history" SET "effective_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("effective_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("effective_at"), '')) IS NOT NULL
    AND "effective_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("effective_at"), ''));
UPDATE "entity_tag_history" SET "expires_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), '')) IS NOT NULL
    AND "expires_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''));
UPDATE "entity_tags" SET "added_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("added_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("added_at"), '')) IS NOT NULL
    AND "added_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("added_at"), ''));
UPDATE "entity_tags" SET "deactivated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("deactivated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("deactivated_at"), '')) IS NOT NULL
    AND "deactivated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("deactivated_at"), ''));
UPDATE "entity_tags" SET "expires_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), '')) IS NOT NULL
    AND "expires_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''));
UPDATE "entity_watchlist" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "job_steps" SET "completed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), '')) IS NOT NULL
    AND "completed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''));
UPDATE "job_steps" SET "next_retry_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("next_retry_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("next_retry_at"), '')) IS NOT NULL
    AND "next_retry_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("next_retry_at"), ''));
UPDATE "job_steps" SET "started_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), '')) IS NOT NULL
    AND "started_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''));
UPDATE "jobs" SET "completed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), '')) IS NOT NULL
    AND "completed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''));
UPDATE "jobs" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "jobs" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "kol_candidates" SET "account_created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("account_created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("account_created_at"), '')) IS NOT NULL
    AND "account_created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("account_created_at"), ''));
UPDATE "kol_candidates" SET "first_seen_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("first_seen_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("first_seen_at"), '')) IS NOT NULL
    AND "first_seen_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("first_seen_at"), ''));
UPDATE "kol_candidates" SET "last_enriched_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_enriched_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_enriched_at"), '')) IS NOT NULL
    AND "last_enriched_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_enriched_at"), ''));
UPDATE "kol_candidates" SET "last_seen_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen_at"), '')) IS NOT NULL
    AND "last_seen_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen_at"), ''));
UPDATE "kol_create_audit" SET "at_utc" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("at_utc"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("at_utc"), '')) IS NOT NULL
    AND "at_utc" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("at_utc"), ''));
UPDATE "kol_create_audit" SET "reviewed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("reviewed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("reviewed_at"), '')) IS NOT NULL
    AND "reviewed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("reviewed_at"), ''));
UPDATE "kol_enrichment" SET "fetched_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("fetched_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("fetched_at"), '')) IS NOT NULL
    AND "fetched_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("fetched_at"), ''));
UPDATE "kol_extract_runs" SET "completed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), '')) IS NOT NULL
    AND "completed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''));
UPDATE "kol_extract_runs" SET "started_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), '')) IS NOT NULL
    AND "started_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''));
UPDATE "kol_follow_edges" SET "fetched_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("fetched_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("fetched_at"), '')) IS NOT NULL
    AND "fetched_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("fetched_at"), ''));
UPDATE "kol_handle_resolution_conflicts" SET "detected_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("detected_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("detected_at"), '')) IS NOT NULL
    AND "detected_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("detected_at"), ''));
UPDATE "kol_handle_resolution_conflicts" SET "resolved_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("resolved_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("resolved_at"), '')) IS NOT NULL
    AND "resolved_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("resolved_at"), ''));
UPDATE "kol_operator_relationships" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "media_assets" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "media_embeddings" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "media_quality" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "media_rec_events" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
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
UPDATE "mod_slot_sessions" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "mod_slot_sessions" SET "ended_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("ended_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("ended_at"), '')) IS NOT NULL
    AND "ended_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("ended_at"), ''));
UPDATE "mod_slot_sessions" SET "started_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), '')) IS NOT NULL
    AND "started_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''));
UPDATE "operator_meme_budget" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "operator_reply_quota" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "operator_work_events" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "operator_work_events" SET "occurred_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("occurred_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("occurred_at"), '')) IS NOT NULL
    AND "occurred_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("occurred_at"), ''));
UPDATE "org_entitlements" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "org_entitlements" SET "ended_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("ended_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("ended_at"), '')) IS NOT NULL
    AND "ended_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("ended_at"), ''));
UPDATE "org_entitlements" SET "started_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), '')) IS NOT NULL
    AND "started_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''));
UPDATE "org_entitlements" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
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
UPDATE "project_profiles_external" SET "last_enriched_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_enriched_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_enriched_at"), '')) IS NOT NULL
    AND "last_enriched_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_enriched_at"), ''));
UPDATE "project_profiles_external" SET "last_used_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_used_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_used_at"), '')) IS NOT NULL
    AND "last_used_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_used_at"), ''));
UPDATE "prospect_scores" SET "graduated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("graduated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("graduated_at"), '')) IS NOT NULL
    AND "graduated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("graduated_at"), ''));
UPDATE "prospect_scores" SET "rejected_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("rejected_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("rejected_at"), '')) IS NOT NULL
    AND "rejected_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("rejected_at"), ''));
UPDATE "prospect_scores" SET "scored_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("scored_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("scored_at"), '')) IS NOT NULL
    AND "scored_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("scored_at"), ''));
UPDATE "relay_chat_bindings" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "relay_chat_bindings" SET "last_seen_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen_at"), '')) IS NOT NULL
    AND "last_seen_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen_at"), ''));
UPDATE "relay_chats" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "relay_clients" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "relay_clients" SET "last_polled_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_polled_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_polled_at"), '')) IS NOT NULL
    AND "last_polled_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_polled_at"), ''));
UPDATE "relay_member_identities" SET "linked_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("linked_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("linked_at"), '')) IS NOT NULL
    AND "linked_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("linked_at"), ''));
UPDATE "relay_member_preferences" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "relay_member_roles" SET "granted_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("granted_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("granted_at"), '')) IS NOT NULL
    AND "granted_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("granted_at"), ''));
UPDATE "relay_members" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "relay_messages" SET "received_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("received_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("received_at"), '')) IS NOT NULL
    AND "received_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("received_at"), ''));
UPDATE "relay_operator_heartbeat" SET "last_seen" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen"), '')) IS NOT NULL
    AND "last_seen" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen"), ''));
UPDATE "relay_opportunity_feedback" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "relay_opportunity_operator_state" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "relay_processed_updates" SET "processed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("processed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("processed_at"), '')) IS NOT NULL
    AND "processed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("processed_at"), ''));
UPDATE "relay_publication_jobs" SET "claimed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("claimed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("claimed_at"), '')) IS NOT NULL
    AND "claimed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("claimed_at"), ''));
UPDATE "relay_publication_jobs" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "relay_publication_jobs" SET "next_attempt_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("next_attempt_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("next_attempt_at"), '')) IS NOT NULL
    AND "next_attempt_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("next_attempt_at"), ''));
UPDATE "relay_publications" SET "published_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("published_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("published_at"), '')) IS NOT NULL
    AND "published_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("published_at"), ''));
UPDATE "relay_quality_accounts" SET "added_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("added_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("added_at"), '')) IS NOT NULL
    AND "added_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("added_at"), ''));
UPDATE "relay_quality_tweets" SET "first_seen_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("first_seen_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("first_seen_at"), '')) IS NOT NULL
    AND "first_seen_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("first_seen_at"), ''));
UPDATE "relay_quality_tweets" SET "posted_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), '')) IS NOT NULL
    AND "posted_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''));
UPDATE "relay_reply_notifications" SET "dismissed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("dismissed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("dismissed_at"), '')) IS NOT NULL
    AND "dismissed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("dismissed_at"), ''));
UPDATE "relay_reply_notifications" SET "notified_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("notified_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("notified_at"), '')) IS NOT NULL
    AND "notified_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("notified_at"), ''));
UPDATE "relay_reply_notifications" SET "replied_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("replied_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("replied_at"), '')) IS NOT NULL
    AND "replied_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("replied_at"), ''));
UPDATE "relay_reply_opportunities" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "relay_reply_opportunities" SET "expires_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), '')) IS NOT NULL
    AND "expires_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''));
UPDATE "relay_search_windows" SET "completed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), '')) IS NOT NULL
    AND "completed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''));
UPDATE "relay_submission_reactions" SET "reacted_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("reacted_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("reacted_at"), '')) IS NOT NULL
    AND "reacted_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("reacted_at"), ''));
UPDATE "relay_submissions" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "relay_submissions" SET "expires_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), '')) IS NOT NULL
    AND "expires_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''));
UPDATE "relay_submissions" SET "resolved_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("resolved_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("resolved_at"), '')) IS NOT NULL
    AND "resolved_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("resolved_at"), ''));
UPDATE "relay_sweep_config" SET "last_sweep_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_sweep_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_sweep_at"), '')) IS NOT NULL
    AND "last_sweep_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_sweep_at"), ''));
UPDATE "relay_sweep_config" SET "sweep_requested_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("sweep_requested_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("sweep_requested_at"), '')) IS NOT NULL
    AND "sweep_requested_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("sweep_requested_at"), ''));
UPDATE "relay_sweep_config" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "relay_sweep_cursor" SET "updated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), '')) IS NOT NULL
    AND "updated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("updated_at"), ''));
UPDATE "relay_topic_picks" SET "picked_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("picked_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("picked_at"), '')) IS NOT NULL
    AND "picked_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("picked_at"), ''));
UPDATE "relay_topic_suggestions" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "relay_topic_suggestions" SET "refreshed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("refreshed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("refreshed_at"), '')) IS NOT NULL
    AND "refreshed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("refreshed_at"), ''));
UPDATE "relay_trending_stories" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "relay_trending_stories" SET "expires_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), '')) IS NOT NULL
    AND "expires_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("expires_at"), ''));
UPDATE "relay_trending_stories" SET "first_seen_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("first_seen_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("first_seen_at"), '')) IS NOT NULL
    AND "first_seen_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("first_seen_at"), ''));
UPDATE "relay_trending_stories" SET "last_seen_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen_at"), '')) IS NOT NULL
    AND "last_seen_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_seen_at"), ''));
UPDATE "relay_tweet_snapshots" SET "taken_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("taken_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("taken_at"), '')) IS NOT NULL
    AND "taken_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("taken_at"), ''));
UPDATE "relay_tweets" SET "fetched_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("fetched_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("fetched_at"), '')) IS NOT NULL
    AND "fetched_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("fetched_at"), ''));
UPDATE "relay_tweets" SET "posted_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), '')) IS NOT NULL
    AND "posted_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''));
UPDATE "reply_campaign_assignments" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "reply_campaign_assignments" SET "posted_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), '')) IS NOT NULL
    AND "posted_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''));
UPDATE "reply_campaigns" SET "closed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("closed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("closed_at"), '')) IS NOT NULL
    AND "closed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("closed_at"), ''));
UPDATE "reply_campaigns" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "reply_campaigns" SET "won_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("won_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("won_at"), '')) IS NOT NULL
    AND "won_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("won_at"), ''));
UPDATE "reply_outcomes" SET "posted_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), '')) IS NOT NULL
    AND "posted_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("posted_at"), ''));
UPDATE "reply_outcomes" SET "recorded_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("recorded_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("recorded_at"), '')) IS NOT NULL
    AND "recorded_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("recorded_at"), ''));
UPDATE "reply_suggestions" SET "generated_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("generated_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("generated_at"), '')) IS NOT NULL
    AND "generated_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("generated_at"), ''));
UPDATE "sync_runs" SET "completed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), '')) IS NOT NULL
    AND "completed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''));
UPDATE "sync_runs" SET "started_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), '')) IS NOT NULL
    AND "started_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''));
UPDATE "tweetbank_entries" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "tweetbank_entries" SET "used_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("used_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("used_at"), '')) IS NOT NULL
    AND "used_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("used_at"), ''));
UPDATE "watchlist_snapshots" SET "snapshot_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("snapshot_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("snapshot_at"), '')) IS NOT NULL
    AND "snapshot_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("snapshot_at"), ''));
UPDATE "webhook_subscriptions" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "webhook_subscriptions" SET "last_failure_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_failure_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_failure_at"), '')) IS NOT NULL
    AND "last_failure_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("last_failure_at"), ''));
UPDATE "workflow_events" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "workflow_runs" SET "completed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), '')) IS NOT NULL
    AND "completed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''));
UPDATE "workflow_runs" SET "created_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), '')) IS NOT NULL
    AND "created_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("created_at"), ''));
UPDATE "workflow_runs" SET "started_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), '')) IS NOT NULL
    AND "started_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''));
UPDATE "workflow_steps" SET "completed_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), '')) IS NOT NULL
    AND "completed_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("completed_at"), ''));
UPDATE "workflow_steps" SET "started_at" = strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''))
  WHERE strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), '')) IS NOT NULL
    AND "started_at" <> strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM("started_at"), ''));

-- Bring the schema version forward. Every migration in this chain ends this way.
UPDATE schema_version SET version = 90 WHERE version < 90;
