-- 076_content_deck.sql
-- Content Deck (Phase 1 candidate substrate) -- see ~/sable-workspace/CONTENT_DECK_MASTERPLAN.md.
-- The durable home for the ambient generate->swipe->schedule loop's candidates + the swipe log.
-- OPS-tooling tables. org_id is the scope wall. NO cost column, ever.
--
-- Three tables:
--   content_candidates       -- the ambient queue (one row per generated candidate)
--   content_deck_decisions   -- the swipe log (keep/reject/skip + pairwise duels) for Elo/BT + training
--   content_deck_operator_state -- per-operator dismiss/snooze (mirrors relay_opportunity_operator_state)
--
-- Design decisions carried from the audit (plan section 3/3.2 + Codex round-1):
--   * org_id REFERENCES orgs(org_id) -- NOT relay_clients (producers feed from tweetbank/compose/meme,
--     which work for any org, not only relay-enabled ones). DI-1.
--   * content_deck_decisions.candidate_id is a NO-FK learning-join (media_rec_events precedent), so the
--     Elo/keep preference signal survives a candidate soft-expiry/purge. DI-NEW-2.
--   * content_deck_operator_state.candidate_id FK ON DELETE CASCADE -- ephemeral per-op state.
--   * candidates SOFT-expire (status='expired'), no physical DELETE in normal operation.
--   * pair_loser_id is another candidate ref. record_deck_decision enforces same-org (no FK here -- a
--     hard FK would break the no-FK survive-purge property of the decisions log).
-- Comment hygiene: NO semicolons inside double-dash comment lines (the runner splits on the literal char).

CREATE TABLE content_candidates (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id           TEXT NOT NULL REFERENCES orgs(org_id),
  kind             TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'pending',
  target_handle    TEXT,
  payload_json     TEXT NOT NULL,
  media_content_id TEXT,
  source           TEXT NOT NULL,
  score            REAL,
  score_reason     TEXT,
  tell_score       REAL,
  dedupe_key       TEXT,
  expires_at       TEXT,
  created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  CHECK (kind IN ('clip', 'tweet', 'thread', 'quote_card', 'meme', 'copypasta')),
  CHECK (status IN ('pending', 'kept', 'scheduled', 'posted', 'rejected', 'expired'))
);

CREATE INDEX content_candidates_by_org_status ON content_candidates (org_id, status, score);
CREATE INDEX content_candidates_by_dedupe ON content_candidates (org_id, dedupe_key);

CREATE TABLE content_deck_decisions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id  INTEGER NOT NULL,
  org_id        TEXT NOT NULL REFERENCES orgs(org_id),
  actor         TEXT NOT NULL,
  actor_kind    TEXT NOT NULL,
  decision      TEXT NOT NULL,
  surface       TEXT NOT NULL,
  pair_loser_id INTEGER,
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  CHECK (actor_kind IN ('operator', 'community')),
  CHECK (decision IN ('keep', 'reject', 'skip', 'schedule', 'post')),
  CHECK (surface IN ('web', 'discord'))
);

CREATE INDEX content_deck_decisions_by_candidate ON content_deck_decisions (org_id, candidate_id);
CREATE INDEX content_deck_decisions_by_actor ON content_deck_decisions (org_id, actor, created_at);

CREATE TABLE content_deck_operator_state (
  candidate_id    INTEGER NOT NULL REFERENCES content_candidates (id) ON DELETE CASCADE,
  operator_handle TEXT NOT NULL,
  state           TEXT NOT NULL,
  snooze_until    TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (candidate_id, operator_handle),
  CHECK (state IN ('dismissed', 'snoozed'))
);

UPDATE schema_version SET version = 76 WHERE version < 76;
