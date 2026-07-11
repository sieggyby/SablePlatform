-- 084_content_duels.sql
-- Durable registry of OPEN community duels (the sable-roles /duel game) so a 24h duel
-- survives a bot restart. discord.py Views are in-memory: on restart the vote buttons die
-- and the auto-reveal never fires. This table is the source of truth the bot rebinds from --
--   * PERSISTENT VIEW: on a button click the bot looks up the duel by message_id (the
--     persistent view is a single stateless instance shared across all duel messages).
--   * DURABLE CLOSE: a background sweep closes any row whose deadline has passed, incl. a
--     startup pass that catches duels that expired while the bot was down.
--   * PER-CHANNEL LOCK: one OPEN row per channel is the restart-safe "a duel is live here".
--
-- card_a_json / card_b_json are RENDERED-CARD SNAPSHOTS ({id,kind,text,author,engagement,
-- engagement_as_of}) captured at post time, so the close reveal never depends on the
-- content_candidates rows still existing (they can expire/GC). The VOTES themselves stay in
-- content_deck_decisions -- the tally is counted from there since opened_at -- so this table
-- only carries the duel's identity + render snapshot + lifecycle.
-- Comment hygiene: NO semicolons inside double-dash comment lines (runner splits on the char).

CREATE TABLE content_duels (
  message_id  TEXT PRIMARY KEY,
  org_id      TEXT NOT NULL REFERENCES orgs(org_id),
  guild_id    TEXT NOT NULL,
  channel_id  TEXT NOT NULL,
  card_a_json TEXT NOT NULL,
  card_b_json TEXT NOT NULL,
  opened_at   TEXT NOT NULL,
  deadline    TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'open',
  closed_at   TEXT,
  CHECK (status IN ('open', 'closed'))
);

CREATE INDEX content_duels_due ON content_duels (status, deadline);
CREATE INDEX content_duels_by_channel ON content_duels (channel_id, status);

UPDATE schema_version SET version = 84 WHERE version < 84;
