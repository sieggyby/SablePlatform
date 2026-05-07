-- Migration 032: SableKOL bank tables.
--
-- Three tables for the SableKOL Phase 0 bank-backed KOL matcher:
--   kol_candidates                     — bank rows. Surrogate PK so multiple rows can
--                                        share handle_normalized when is_unresolved=1.
--                                        A partial unique index enforces at most one
--                                        LIVE row per normalized handle.
--   project_profiles_external          — lite project-profile cache for path-(ii)
--                                        external handles. last_enriched_at drives a
--                                        7-day TTL on paid_basic rows.
--   kol_handle_resolution_conflicts    — audit/triage log when paid enrichment exposes
--                                        a recycled-handle / twitter_id collision.
--
-- See ~/Projects/SableKOL/PLAN.md for the design rationale.

CREATE TABLE IF NOT EXISTS kol_candidates (
    candidate_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    twitter_id              TEXT,
    handle_normalized       TEXT NOT NULL,
    is_unresolved           INTEGER NOT NULL DEFAULT 0,
    handle_history_json     TEXT NOT NULL DEFAULT '[]',
    display_name            TEXT,
    bio_snapshot            TEXT,
    followers_snapshot      INTEGER,
    discovery_sources_json  TEXT NOT NULL DEFAULT '[]',
    first_seen_at           TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at            TEXT NOT NULL DEFAULT (datetime('now')),
    archetype_tags_json     TEXT NOT NULL DEFAULT '[]',
    sector_tags_json        TEXT NOT NULL DEFAULT '[]',
    sable_relationship_json TEXT NOT NULL DEFAULT '{"communities":[],"operators":[]}',
    enrichment_tier         TEXT NOT NULL DEFAULT 'none',
    last_enriched_at        TEXT,
    status                  TEXT NOT NULL DEFAULT 'active',
    manual_notes            TEXT
);

-- At most one LIVE (is_unresolved=0) row per normalized handle. Unresolved duplicates
-- are allowed and tracked via kol_handle_resolution_conflicts.
CREATE UNIQUE INDEX IF NOT EXISTS idx_kol_candidates_handle_live
    ON kol_candidates(handle_normalized) WHERE is_unresolved = 0;

CREATE INDEX IF NOT EXISTS idx_kol_candidates_twitter_id
    ON kol_candidates(twitter_id) WHERE twitter_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_kol_candidates_status
    ON kol_candidates(status);

CREATE TABLE IF NOT EXISTS project_profiles_external (
    handle_normalized   TEXT PRIMARY KEY,
    twitter_id          TEXT,
    sector_tags_json    TEXT NOT NULL DEFAULT '[]',
    themes_json         TEXT NOT NULL DEFAULT '[]',
    profile_blob        TEXT,
    enrichment_source   TEXT NOT NULL DEFAULT 'manual_only',
    last_enriched_at    TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kol_handle_resolution_conflicts (
    conflict_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    incoming_candidate_id   INTEGER NOT NULL,
    existing_candidate_id   INTEGER NOT NULL,
    resolved_twitter_id     TEXT,
    detected_at             TEXT NOT NULL DEFAULT (datetime('now')),
    resolution_state        TEXT NOT NULL DEFAULT 'open',
    resolved_at             TEXT,
    notes                   TEXT,
    FOREIGN KEY (incoming_candidate_id) REFERENCES kol_candidates(candidate_id),
    FOREIGN KEY (existing_candidate_id) REFERENCES kol_candidates(candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_kol_conflicts_state
    ON kol_handle_resolution_conflicts(resolution_state);

UPDATE schema_version SET version = 32 WHERE version < 32;
