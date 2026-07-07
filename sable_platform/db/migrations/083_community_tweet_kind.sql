-- 083_community_tweet_kind.sql
-- Adds 'community_tweet' to the content_candidates kind CHECK -- ingested REAL community tweets
-- served in the sable-roles /duel "which popped" game (COMMUNITY_DUEL_PLAN.md Phase A). These rows
-- are duel-only content: target_handle is always NULL (structurally unschedulable), they are
-- excluded from every operator-facing deck surface, and their duels never fold into the content
-- Elo. The kind exists so the wall can key on it.
--
-- SQLite cannot ALTER a CHECK, so this is a table rebuild -- and content_candidates is NOT a leaf
-- table: content_publish_jobs.candidate_id and content_deck_operator_state.candidate_id both
-- reference it ON DELETE CASCADE, and every connection runs with PRAGMA foreign_keys=ON. A naive
-- single-table rebuild (068-style) would implicit-DELETE the old parent and CASCADE-WIPE both
-- children. So all THREE tables rebuild together:
--   1. create _new tables (children FK -> content_candidates_new) + copy rows parent-first
--   2. drop old children (leaves now), then the old parent (nothing references it anymore)
--   3. rename _new tables back -- SQLite rewrites the children's FK clauses on parent rename
--   4. recreate the canonical indexes (index defs die with the old tables)
-- content_deck_decisions is a NO-FK learning-join and is untouched.
-- Comment hygiene: NO semicolons inside double-dash comment lines (the runner splits on the char).

CREATE TABLE content_candidates_new (
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
  CHECK (kind IN ('clip', 'tweet', 'thread', 'quote_card', 'meme', 'copypasta', 'community_tweet')),
  CHECK (status IN ('pending', 'kept', 'scheduled', 'posted', 'rejected', 'expired'))
);

INSERT INTO content_candidates_new (id, org_id, kind, status, target_handle, payload_json,
  media_content_id, source, score, score_reason, tell_score, dedupe_key, expires_at, created_at)
SELECT id, org_id, kind, status, target_handle, payload_json,
  media_content_id, source, score, score_reason, tell_score, dedupe_key, expires_at, created_at
FROM content_candidates;

CREATE TABLE content_publish_jobs_new (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id    INTEGER NOT NULL REFERENCES content_candidates_new (id) ON DELETE CASCADE,
  org_id          TEXT NOT NULL REFERENCES orgs (org_id),
  target_handle   TEXT NOT NULL,
  release_state   TEXT NOT NULL DEFAULT 'scheduled',
  publish_at      TEXT NOT NULL,
  next_attempt_at TEXT,
  attempt_count   INTEGER NOT NULL DEFAULT 0,
  claimed_at      TEXT,
  handed_off_at   TEXT,
  posted_ref      TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  CHECK (release_state IN ('scheduled', 'due', 'claimed', 'handed_off', 'posted', 'canceled')),
  CONSTRAINT ck_content_publish_jobs_publish_at_utc CHECK (
    publish_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
  )
);

INSERT INTO content_publish_jobs_new (id, candidate_id, org_id, target_handle, release_state,
  publish_at, next_attempt_at, attempt_count, claimed_at, handed_off_at, posted_ref,
  created_at, updated_at)
SELECT id, candidate_id, org_id, target_handle, release_state,
  publish_at, next_attempt_at, attempt_count, claimed_at, handed_off_at, posted_ref,
  created_at, updated_at
FROM content_publish_jobs;

CREATE TABLE content_deck_operator_state_new (
  candidate_id    INTEGER NOT NULL REFERENCES content_candidates_new (id) ON DELETE CASCADE,
  operator_handle TEXT NOT NULL,
  state           TEXT NOT NULL,
  snooze_until    TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (candidate_id, operator_handle),
  CHECK (state IN ('dismissed', 'snoozed'))
);

INSERT INTO content_deck_operator_state_new (candidate_id, operator_handle, state, snooze_until, created_at)
SELECT candidate_id, operator_handle, state, snooze_until, created_at
FROM content_deck_operator_state;

DROP TABLE content_deck_operator_state;

DROP TABLE content_publish_jobs;

DROP TABLE content_candidates;

ALTER TABLE content_candidates_new RENAME TO content_candidates;

ALTER TABLE content_publish_jobs_new RENAME TO content_publish_jobs;

ALTER TABLE content_deck_operator_state_new RENAME TO content_deck_operator_state;

CREATE INDEX content_candidates_by_org_status ON content_candidates (org_id, status, score);

CREATE INDEX content_candidates_by_dedupe ON content_candidates (org_id, dedupe_key);

CREATE INDEX content_publish_jobs_by_org_state ON content_publish_jobs (org_id, release_state, publish_at);

CREATE INDEX content_publish_jobs_due ON content_publish_jobs (release_state, publish_at);

CREATE INDEX content_publish_jobs_by_candidate ON content_publish_jobs (candidate_id);

UPDATE schema_version SET version = 83 WHERE version < 83;
