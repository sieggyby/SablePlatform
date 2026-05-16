-- Migration 043: discord_streak_events for fit-check streak bot (PLAN.md SS10)

CREATE TABLE IF NOT EXISTS discord_streak_events (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id                 TEXT NOT NULL,
    guild_id               TEXT NOT NULL,
    channel_id             TEXT NOT NULL,
    post_id                TEXT NOT NULL,
    user_id                TEXT NOT NULL,
    posted_at              TEXT NOT NULL,
    counted_for_day        TEXT NOT NULL,
    attachment_count       INTEGER NOT NULL DEFAULT 0,
    image_attachment_count INTEGER NOT NULL DEFAULT 0,
    reaction_score         INTEGER NOT NULL DEFAULT 0,
    counts_for_streak      INTEGER NOT NULL DEFAULT 1,
    invalidated_at         TEXT,
    invalidated_reason     TEXT,
    ingest_source          TEXT NOT NULL DEFAULT 'gateway',
    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (guild_id, post_id)
);

CREATE INDEX IF NOT EXISTS idx_discord_streak_events_org_day
    ON discord_streak_events (org_id, counted_for_day);
CREATE INDEX IF NOT EXISTS idx_discord_streak_events_user_day
    ON discord_streak_events (org_id, user_id, counted_for_day);
CREATE INDEX IF NOT EXISTS idx_discord_streak_events_channel_posted
    ON discord_streak_events (org_id, channel_id, posted_at);

-- Index supports the best-fit-ever query in /streak (sort by reaction_score per user)
CREATE INDEX IF NOT EXISTS idx_discord_streak_events_user_reactions
    ON discord_streak_events (org_id, user_id, reaction_score DESC);

UPDATE schema_version SET version = 43 WHERE version < 43;
