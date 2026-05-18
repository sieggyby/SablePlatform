-- Migration 051: discord_scoring_config for Scored Mode V2 Pass B.
-- One row per guild, UNIQUE(guild_id). Default state = 'off' -- safety floor.
-- First /scoring set silent by a mod is what flips a guild into calibration
-- mode. Off -> Silent -> Revealed is the only sanctioned transition path,
-- but reverses (Revealed -> Silent / -> Off) are also allowed as rollback.
-- Tunables (thresholds, windows) ship with table defaults from plan sec 6.3.
-- /scoring config (per-guild override) is V2 deferred -- callers read these
-- columns directly for now.
--
-- Comment-hygiene reminder: no semicolons inside double-dash comment lines.
-- The runner in connection.py splits on the literal semicolon character.

CREATE TABLE IF NOT EXISTS discord_scoring_config (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id                   TEXT NOT NULL,
    guild_id                 TEXT NOT NULL,
    state                    TEXT NOT NULL DEFAULT 'off',
    state_changed_by         TEXT,
    state_changed_at         TEXT,
    reaction_threshold       INTEGER NOT NULL DEFAULT 10,
    thread_message_threshold INTEGER NOT NULL DEFAULT 100,
    reveal_window_days       INTEGER NOT NULL DEFAULT 7,
    reveal_min_age_minutes   INTEGER NOT NULL DEFAULT 10,
    curve_window_days        INTEGER NOT NULL DEFAULT 30,
    cold_start_min_pool      INTEGER NOT NULL DEFAULT 20,
    model_id                 TEXT NOT NULL DEFAULT 'claude-sonnet-4-6',
    prompt_version           TEXT NOT NULL DEFAULT 'rubric_v1',
    created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (guild_id)
);

UPDATE schema_version SET version = 51 WHERE version < 51;
