-- Migration 045: discord_guild_config for sable-roles V2 (relax-mode toggle + burn-me global mode)
-- One row per configured guild. Created lazily by the first mod command that mutates it.
-- relax_mode_on: 0/1 -- when 1, sable-roles skips delete+DM on text-only posts and skips auto-thread on image posts in the fit-check channel.
-- current_burn_mode: 'once' or 'persist' -- global default mode applied to /burn-me opt-ins (V2 burn-me feature).

CREATE TABLE IF NOT EXISTS discord_guild_config (
    guild_id          TEXT PRIMARY KEY,
    relax_mode_on     INTEGER NOT NULL DEFAULT 0,
    current_burn_mode TEXT NOT NULL DEFAULT 'once',
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_by        TEXT NOT NULL
);

UPDATE schema_version SET version = 45 WHERE version < 45;
