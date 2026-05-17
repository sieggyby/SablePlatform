-- Migration 054: discord_state_pins for the state-pin surface.
--
-- One row per (guild_id, characteristic). Tracks the currently-pinned
-- "stitzy state" message in the per-guild ops channel so the bot can
-- rotate pins on state changes and recover from in-flight crashes by
-- comparing the pinned-message id against this pointer.
--
-- UNIQUE (guild_id, characteristic) enforces one-pin-per-dimension at
-- the schema level. The optimistic-lock UPDATE in upsert_state_pin
-- uses WHERE updated_at = :expected to detect lost races.
--
-- Comment-hygiene reminder: no semicolons inside double-dash comment lines.
-- The runner in connection.py splits on the literal semicolon character.

CREATE TABLE IF NOT EXISTS discord_state_pins (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        TEXT NOT NULL,
    characteristic  TEXT NOT NULL,
    channel_id      TEXT NOT NULL,
    message_id      TEXT NOT NULL,
    posted_at       TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (guild_id, characteristic)
);

CREATE INDEX IF NOT EXISTS idx_discord_state_pins_guild
    ON discord_state_pins (guild_id);

UPDATE schema_version SET version = 54 WHERE version < 54;
