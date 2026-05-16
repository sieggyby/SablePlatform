-- Migration 047: /roast V2 peer economy + personalization layer (sable-roles).
-- Adds 6 tables and one column. Mirrors plan section 3 of
-- ~/Projects/SolStitch/internal/roast_v1_v2_personalization_plan.md (post-audit).
--
-- 3.1 discord_burn_blocklist          — sticky /stop-pls opt-out list
-- 3.2 discord_peer_roast_tokens       — peer-economy token ledger (monthly + restoration)
-- 3.3 discord_peer_roast_flags        — peer-roast flag log (target/witness/self)
-- 3.4 discord_user_observations       — rollup of recent user activity per guild
-- 3.5 discord_user_vibes              — LLM-summarized per-user vibe block
-- 3.7 discord_message_observations    — raw per-message observation log (rollup source)
-- 3.6 ALTER discord_guild_config      — personalize_mode_on toggle
--
-- Comment-hygiene reminder: no semicolons inside double-dash comment lines.
-- The runner in connection.py splits on the literal semicolon character.

-- 3.1 Sticky stop-pls blocklist
CREATE TABLE IF NOT EXISTS discord_burn_blocklist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    blocked_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(guild_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_discord_burn_blocklist_user
    ON discord_burn_blocklist (user_id, guild_id);

-- 3.2 Peer-roast token ledger.
-- UNIQUE(guild_id, actor_user_id, year_month, source) blocks the
-- concurrent-double-grant race (post-audit BLOCKER 2). Grants MUST use
-- INSERT ... ON CONFLICT DO NOTHING and then SELECT the row back to
-- distinguish "I granted" from "someone else just granted".
-- The partial index on consumed targets accelerates the per-target
-- volume-cap query (3/month) on the peer-roast hot path.
CREATE TABLE IF NOT EXISTS discord_peer_roast_tokens (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id                 TEXT NOT NULL,
    actor_user_id            TEXT NOT NULL,
    granted_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    source                   TEXT NOT NULL CHECK (source IN ('monthly', 'streak_restoration')),
    year_month               TEXT NOT NULL,
    consumed_at              TEXT,
    consumed_on_post_id      TEXT,
    consumed_target_user_id  TEXT,
    UNIQUE(guild_id, actor_user_id, year_month, source)
);

CREATE INDEX IF NOT EXISTS idx_discord_peer_roast_tokens_actor_month
    ON discord_peer_roast_tokens (actor_user_id, guild_id, year_month);

CREATE INDEX IF NOT EXISTS idx_discord_peer_roast_tokens_target_month
    ON discord_peer_roast_tokens (consumed_target_user_id, guild_id, year_month)
    WHERE consumed_at IS NOT NULL;

-- 3.3 Peer-roast flag log.
-- reactor_user_id distinguishes target-self-flags from third-party flags so
-- mods can weight reports. bot_reply_id disambiguates when multiple roasts
-- (mod + peer) share the same target fit post_id.
CREATE TABLE IF NOT EXISTS discord_peer_roast_flags (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id          TEXT NOT NULL,
    target_user_id    TEXT NOT NULL,
    actor_user_id     TEXT NOT NULL,
    post_id           TEXT NOT NULL,
    bot_reply_id      TEXT NOT NULL,
    reactor_user_id   TEXT NOT NULL,
    flagged_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_discord_peer_roast_flags_target
    ON discord_peer_roast_flags (target_user_id, guild_id, flagged_at);

CREATE INDEX IF NOT EXISTS idx_discord_peer_roast_flags_bot_reply
    ON discord_peer_roast_flags (bot_reply_id);

-- 3.7 Raw per-message observation log (post-audit BLOCKER 7 source-of-truth).
-- Written by an on_message listener (separate from the fitcheck handler)
-- watching every channel listed in OBSERVATION_CHANNELS. Content truncated
-- at 500 chars to bound row size. Reactions GIVEN BY this user are merged
-- into reactions_given_json via on_raw_reaction_add (UPSERT pattern).
-- TTL: nightly GC drops rows older than VIBE_OBSERVATION_WINDOW_DAYS + 7.
-- /stop-pls and leave-guild both DELETE for (guild_id, user_id).
-- This table must be created BEFORE discord_user_observations because the
-- rollup reads from it, but no SQL-level FK is required (rollup is
-- best-effort + idempotent).
CREATE TABLE IF NOT EXISTS discord_message_observations (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id              TEXT NOT NULL,
    channel_id            TEXT NOT NULL,
    message_id            TEXT NOT NULL,
    user_id               TEXT NOT NULL,
    content_truncated     TEXT,
    reactions_given_json  TEXT,
    posted_at             TEXT NOT NULL,
    captured_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(guild_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_discord_message_observations_user_time
    ON discord_message_observations (user_id, guild_id, posted_at);

CREATE INDEX IF NOT EXISTS idx_discord_message_observations_gc
    ON discord_message_observations (captured_at);

-- 3.4 User observation rollups (populated by daily cron from 3.7 raw rows).
CREATE TABLE IF NOT EXISTS discord_user_observations (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id                   TEXT NOT NULL,
    user_id                    TEXT NOT NULL,
    window_start               TEXT NOT NULL,
    window_end                 TEXT NOT NULL,
    message_count              INTEGER NOT NULL DEFAULT 0,
    sample_messages_json       TEXT,
    reaction_emojis_given_json TEXT,
    channels_active_in_json    TEXT,
    computed_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_discord_user_observations_user
    ON discord_user_observations (user_id, guild_id, computed_at);

-- 3.5 User vibes (LLM-summarized weekly). source_observation_id REFERENCES
-- discord_user_observations(id) — this is the FK that pins TABLE_LOAD_ORDER
-- to (observations BEFORE vibes) on Postgres restore. Fields are the five
-- §7.3 JSON output keys, plus a rendered <user_vibe> block ready for
-- injection by §5.3 generate_roast.
CREATE TABLE IF NOT EXISTS discord_user_vibes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id               TEXT NOT NULL,
    user_id                TEXT NOT NULL,
    vibe_block_text        TEXT NOT NULL,
    identity               TEXT,
    activity_rhythm        TEXT,
    reaction_signature     TEXT,
    palette_signals        TEXT,
    tone                   TEXT,
    inferred_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    source_observation_id  INTEGER REFERENCES discord_user_observations(id)
);

CREATE INDEX IF NOT EXISTS idx_discord_user_vibes_user_recent
    ON discord_user_vibes (user_id, guild_id, inferred_at);

-- 3.6 Personalize toggle column on discord_guild_config. Default OFF so
-- guilds that never opt in stay observation-only (no LLM inference fires).
-- INTEGER matches the existing relax_mode_on convention on the same table.
ALTER TABLE discord_guild_config ADD COLUMN personalize_mode_on INTEGER NOT NULL DEFAULT 0;

UPDATE schema_version SET version = 47 WHERE version < 47;
