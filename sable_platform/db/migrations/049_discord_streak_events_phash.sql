-- Migration 049: image_phash on discord_streak_events for Scored Mode V2 Pass A.
-- Captured at post time regardless of scoring state. Powers repost detection
-- (same user, accidental re-upload, LOW severity) and image-theft detection
-- (different user, HIGH severity). Hamming-distance threshold check happens
-- app-side -- SQLite has no popcount, so the index covers candidate fetch and
-- bit-distance is computed in Python via imagehash.
--
-- Comment-hygiene reminder: no semicolons inside double-dash comment lines.
-- The runner in connection.py splits on the literal semicolon character.

ALTER TABLE discord_streak_events ADD COLUMN image_phash TEXT;

CREATE INDEX IF NOT EXISTS idx_discord_streak_events_org_phash
    ON discord_streak_events (org_id, image_phash);

UPDATE schema_version SET version = 49 WHERE version < 49;
