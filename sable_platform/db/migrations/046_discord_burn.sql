-- Migration 046: burn-me opt-in state + random-roast dedup log for sable-roles V2 burn-me.
-- discord_burn_optins: per (guild_id, user_id) opt-in row with mode (once/persist) and audit fields.
-- discord_burn_random_log: append-only log of random-bypass roasts, used for 7d per-target dedup.

CREATE TABLE IF NOT EXISTS discord_burn_optins (
    guild_id     TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    mode         TEXT NOT NULL,
    opted_in_by  TEXT NOT NULL,
    opted_in_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS discord_burn_random_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    roasted_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_discord_burn_random_log_recent
    ON discord_burn_random_log (guild_id, user_id, roasted_at DESC);

UPDATE schema_version SET version = 46 WHERE version < 46;
