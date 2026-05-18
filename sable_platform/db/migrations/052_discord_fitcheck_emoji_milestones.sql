-- Migration 052: discord_fitcheck_emoji_milestones for Scored Mode V2 Pass C.
-- Per-(post_id, emoji_key, milestone) crossing state. Durable across VPS
-- restarts so the bot doesn't re-audit "post X hit 5 reactions on the heart
-- emoji" every time the reveal recompute fires. UNIQUE constraint blocks the
-- double-audit race when two reaction events fire near-simultaneously.
--
-- Why durable instead of an in-memory dict: VPS restarts are routine on the
-- Hetzner box (docker compose restart on every migration + redeploy). An
-- in-mem dict would re-emit a milestone audit row for every post that has
-- already crossed every threshold the first time a recompute fires post-boot.
-- One ALTER + tiny table is cheaper than weekly audit-row spam.
--
-- Comment-hygiene reminder: no semicolons inside double-dash comment lines.
-- The runner in connection.py splits on the literal semicolon character.

CREATE TABLE IF NOT EXISTS discord_fitcheck_emoji_milestones (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id       TEXT NOT NULL,
    guild_id     TEXT NOT NULL,
    post_id      TEXT NOT NULL,
    emoji_key    TEXT NOT NULL,
    milestone    INTEGER NOT NULL,
    crossed_at   TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (guild_id, post_id, emoji_key, milestone)
);

CREATE INDEX IF NOT EXISTS idx_discord_fitcheck_emoji_milestones_post
    ON discord_fitcheck_emoji_milestones (guild_id, post_id);

UPDATE schema_version SET version = 52 WHERE version < 52;
