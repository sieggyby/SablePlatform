-- 087: community_audit_vocab_corpus — the cross-community vocabulary background corpus.
--
-- WHY: deciding whether a phrase is community-COINED or ordinary language currently
-- needs an LLM, because a single community's top phrases are dominated by generic
-- English ("to get", "how do"). The durable answer is contrast: a phrase appearing in
-- MANY audited communities is generic, one unique to a single community is endemic.
-- Measured at n=2 that method killed only 1 of 18 candidates, so it needs roughly
-- 10-20 corpora before it works.
--
-- Every deep audit already computes a top-50 phrase list and discards it. Persisting it
-- builds the background corpus as a byproduct of running the product -- no backfill, no
-- scraping, no spend -- and lets the LLM judge be retired once contrast matches it.
--
-- PRIVACY (R4): phrases and counts ONLY, never message content. A phrase reaching a
-- top-50-by-breadth list is by construction not one person's private message.

CREATE TABLE IF NOT EXISTS community_audit_vocab_corpus (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id         TEXT NOT NULL REFERENCES community_audit_guilds(guild_id),
  run_id           INTEGER,
  phrase           TEXT NOT NULL,
  unique_users     INTEGER NOT NULL DEFAULT 0,
  occurrences      INTEGER NOT NULL DEFAULT 0,
  spread_velocity  REAL,
  first_seen_week  TEXT,
  -- NULL = never judged. Lets a later contrast-based method be scored against the
  -- LLM's verdicts on the same rows before the LLM is retired.
  judged_coined    INTEGER,
  created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- The contrast query is "how many DISTINCT guilds used this phrase", so phrase leads.
CREATE INDEX IF NOT EXISTS idx_vocab_corpus_phrase ON community_audit_vocab_corpus(phrase);
CREATE INDEX IF NOT EXISTS idx_vocab_corpus_guild ON community_audit_vocab_corpus(guild_id);

-- One row per (guild, phrase, run) so a re-audit does not double-count a community
-- toward that phrase's breadth -- which would make its own vocabulary look generic.
CREATE UNIQUE INDEX IF NOT EXISTS idx_vocab_corpus_unique
  ON community_audit_vocab_corpus(guild_id, phrase, run_id);

UPDATE schema_version SET version = 87 WHERE version < 87;
