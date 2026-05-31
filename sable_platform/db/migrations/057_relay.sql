-- 057_relay.sql
-- SableRelay schema (the relay_* table family) -- the multi-tenant bridge
-- between X, Telegram, and Discord for Sable client communities. Built as a
-- SablePlatform module (sable_platform.relay), tables prefixed relay_* to
-- namespace cleanly alongside kol_*, discord_*, org_* tables.
-- Source of truth: SableRelay/PLAN.md section 5.1 (single consolidated
-- migration is the relay convention), with two C1.1-decided corrections:
--
--   1. relay_publication_jobs.state CHECK follows the section-3.1 decided set
--      ('pending','claimed','retry','done','dead') -- NOT the stale section-5.1
--      DDL ('pending','claimed','done','failed','dead'). The publisher loop
--      (C2.4) writes 'retry' and the old literal would reject every retry
--      transition. 'failed' was ambiguous and removed -- 'dead' is the only
--      halted/terminal value. All partial indexes / cleanup statements are
--      consistent with the corrected set (dedupe over pending/claimed/done,
--      stuck-claim cleanup over pending/retry/claimed).
--
--   2. Two tables added beyond section-5.1 for the AutoCM FK reconciliation
--      (companion to D-2) so autocm_drafts source FKs (C3.0) have real targets:
--        - relay_chats  : the chat-id surface for autocm_drafts.source_chat_id
--        - relay_messages : one row PER inbound message (NOT just engaged ones)
--          -- the corpus the digest Volume section + member analytics
--          (cultist_candidates/topic_clusters/score_sentiment/frequent_questions,
--          C3.7) aggregate over -- autocm_drafts cannot serve this because the
--          classifier strong-skips most traffic with no draft row. CANONICAL
--          NAME is relay_messages (the FK target two locked AutoCM docs encode).
--      This reverses the section-5.2 "inbound TG messages are never persisted"
--      retention posture: relay now retains a minimal inbound message/chat
--      surface, GC'd on a bounded window by the C2.4 sweeper.
--
-- org PK is orgs(org_id) (TEXT), not orgs.id. relay_clients.org_id is a TEXT FK
-- to orgs(org_id) -- Relay does not duplicate org identity.
--
-- Comment hygiene: no semicolons inside double-dash comment lines. The runner
-- in connection.py splits on the literal semicolon character.
-- Column conventions (migration 053 contract): counts are INTEGER (never INT),
-- all _at columns are TEXT with the strftime ISO-8601-Z default below (NOT
-- datetime('now'), which emits a space-separated no-Z format that bit prod on
-- 2026-05-17).

CREATE TABLE relay_clients (
  org_id                   TEXT PRIMARY KEY REFERENCES orgs(org_id),
  enabled                  INTEGER NOT NULL DEFAULT 0,
  x_handle_override        TEXT,
  polling_interval_seconds INTEGER NOT NULL DEFAULT 300,
  last_polled_at           TEXT,
  last_seen_x_id           TEXT,
  last_error               TEXT,
  config                   TEXT NOT NULL DEFAULT '{}',
  created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- relay_chats: chat-id surface for autocm_drafts.source_chat_id (C1.1 / D-2).
-- One stable row per (platform, external chat id) an org's relay operates in.
CREATE TABLE relay_chats (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id          TEXT NOT NULL REFERENCES relay_clients(org_id),
  platform        TEXT NOT NULL CHECK (platform IN ('telegram','discord')),
  chat_id         TEXT NOT NULL,
  title           TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE UNIQUE INDEX relay_chats_unique
  ON relay_chats(platform, chat_id);
CREATE INDEX relay_chats_by_org
  ON relay_chats(org_id);

CREATE TABLE relay_chat_bindings (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id          TEXT NOT NULL REFERENCES relay_clients(org_id),
  platform        TEXT NOT NULL CHECK (platform IN ('telegram','discord')),
  chat_id         TEXT NOT NULL,
  role            TEXT NOT NULL CHECK (role IN ('operator','shared','community','broadcast')),
  status          TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','migrated','kicked','disabled')),
  superseded_by_chat_id TEXT,
  last_seen_at    TEXT,
  last_error      TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE UNIQUE INDEX relay_chat_bindings_unique_role
  ON relay_chat_bindings(org_id, platform, role) WHERE status = 'active';
CREATE UNIQUE INDEX relay_chat_bindings_unique_chat
  ON relay_chat_bindings(platform, chat_id) WHERE status = 'active';

CREATE TABLE relay_members (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  display_name    TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE relay_member_identities (
  member_id        INTEGER NOT NULL REFERENCES relay_members(id),
  platform         TEXT NOT NULL CHECK (platform IN ('telegram','x','discord')),
  external_user_id TEXT NOT NULL,
  handle           TEXT,
  linked_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (platform, external_user_id)
);
CREATE INDEX relay_member_identities_by_member
  ON relay_member_identities(member_id);

CREATE TABLE relay_member_roles (
  member_id       INTEGER NOT NULL REFERENCES relay_members(id),
  org_id          TEXT NOT NULL REFERENCES relay_clients(org_id),
  role            TEXT NOT NULL CHECK (role IN ('sable_operator','client_team','admin')),
  granted_by      INTEGER REFERENCES relay_members(id),
  granted_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (member_id, org_id, role)
);
CREATE INDEX relay_member_roles_by_org_role
  ON relay_member_roles(org_id, role);

CREATE TABLE relay_member_preferences (
  member_id       INTEGER NOT NULL REFERENCES relay_members(id),
  org_id          TEXT NOT NULL REFERENCES relay_clients(org_id),
  replies_optin   INTEGER NOT NULL DEFAULT 0,
  mute_until      TEXT,
  updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (member_id, org_id)
);
CREATE INDEX relay_member_preferences_optin
  ON relay_member_preferences(org_id, replies_optin, mute_until);

CREATE TABLE relay_tweets (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  x_id             TEXT UNIQUE NOT NULL,
  x_author_id      TEXT,
  x_author_handle  TEXT NOT NULL,
  text             TEXT,
  media_urls       TEXT NOT NULL DEFAULT '[]',
  is_reply         INTEGER NOT NULL DEFAULT 0,
  in_reply_to_x_id TEXT,
  conversation_x_id TEXT,
  fetched_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  raw              TEXT
);
CREATE INDEX relay_tweets_author ON relay_tweets(x_author_id);

-- relay_messages: one row PER inbound message (C1.1 / D-2). The corpus for the
-- digest Volume section + member analytics (C3.7). autocm_drafts.source_message_id
-- FKs to relay_messages(id). GC'd by the C2.4 sweeper on a bounded window.
CREATE TABLE relay_messages (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id           TEXT NOT NULL REFERENCES relay_clients(org_id),
  chat_id          INTEGER NOT NULL REFERENCES relay_chats(id),
  member_id        INTEGER REFERENCES relay_members(id),
  platform         TEXT NOT NULL CHECK (platform IN ('telegram','discord')),
  external_message_id TEXT NOT NULL,
  external_user_id TEXT,
  text             TEXT,
  reply_to_external_message_id TEXT,
  received_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE UNIQUE INDEX relay_messages_unique
  ON relay_messages(platform, chat_id, external_message_id);
CREATE INDEX relay_messages_org_received
  ON relay_messages(org_id, received_at);
CREATE INDEX relay_messages_member
  ON relay_messages(member_id, received_at);
CREATE INDEX relay_messages_gc
  ON relay_messages(received_at);

CREATE TABLE relay_submissions (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id             TEXT NOT NULL REFERENCES relay_clients(org_id),
  tweet_id           INTEGER NOT NULL REFERENCES relay_tweets(id),
  submitter_id       INTEGER NOT NULL REFERENCES relay_members(id),
  source_chat_id     TEXT NOT NULL,
  source_message_id  TEXT NOT NULL,
  control_message_id TEXT,
  source_role        TEXT NOT NULL CHECK (source_role IN ('operator','shared')),
  note               TEXT,
  status             TEXT NOT NULL CHECK (status IN ('pending','ready_to_publish','published','expired','rejected')),
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  expires_at         TEXT NOT NULL,
  resolved_at        TEXT
);
CREATE INDEX relay_submissions_org_status
  ON relay_submissions(org_id, status, created_at);
CREATE INDEX relay_submissions_expires
  ON relay_submissions(status, expires_at);
CREATE UNIQUE INDEX relay_submissions_one_pending_per_tweet
  ON relay_submissions(org_id, tweet_id) WHERE status IN ('pending','ready_to_publish');
CREATE INDEX relay_submissions_control_lookup
  ON relay_submissions(source_chat_id, control_message_id);

CREATE TABLE relay_submission_reactions (
  submission_id   INTEGER NOT NULL REFERENCES relay_submissions(id),
  member_id       INTEGER NOT NULL REFERENCES relay_members(id),
  emoji           TEXT NOT NULL,
  reacted_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (submission_id, member_id, emoji)
);
CREATE INDEX relay_submission_reactions_by_emoji
  ON relay_submission_reactions(submission_id, emoji);

-- state CHECK = the section-3.1 corrected set: drop 'failed', add 'retry'.
CREATE TABLE relay_publication_jobs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id          TEXT NOT NULL REFERENCES relay_clients(org_id),
  submission_id   INTEGER REFERENCES relay_submissions(id),
  tweet_id        INTEGER NOT NULL REFERENCES relay_tweets(id),
  destination_platform TEXT NOT NULL CHECK (destination_platform IN ('discord','telegram')),
  destination_chat_id  TEXT NOT NULL,
  state           TEXT NOT NULL CHECK (state IN ('pending','claimed','retry','done','dead')),
  attempts        INTEGER NOT NULL DEFAULT 0,
  claimed_by      TEXT,
  claimed_at      TEXT,
  next_attempt_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  last_error      TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX relay_publication_jobs_due
  ON relay_publication_jobs(state, next_attempt_at);
CREATE UNIQUE INDEX relay_publication_jobs_dedupe
  ON relay_publication_jobs(org_id, tweet_id, destination_platform, destination_chat_id)
  WHERE state IN ('pending','claimed','done');

CREATE TABLE relay_publications (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id          TEXT NOT NULL REFERENCES relay_clients(org_id),
  submission_id   INTEGER REFERENCES relay_submissions(id),
  tweet_id        INTEGER NOT NULL REFERENCES relay_tweets(id),
  destination_platform TEXT NOT NULL,
  destination_chat_id  TEXT NOT NULL,
  destination_message_id TEXT NOT NULL,
  published_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE UNIQUE INDEX relay_publications_unique
  ON relay_publications(org_id, tweet_id, destination_platform, destination_chat_id);
CREATE INDEX relay_publications_by_tweet
  ON relay_publications(tweet_id);
CREATE INDEX relay_publications_by_message
  ON relay_publications(destination_platform, destination_chat_id, destination_message_id);

CREATE TABLE relay_reply_opportunities (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id          TEXT NOT NULL REFERENCES relay_clients(org_id),
  tweet_id        INTEGER NOT NULL REFERENCES relay_tweets(id),
  flagger_id      INTEGER NOT NULL REFERENCES relay_members(id),
  origin          TEXT NOT NULL CHECK (origin IN ('explicit_command','reaction','auto_mention')),
  note            TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX relay_reply_opportunities_by_org
  ON relay_reply_opportunities(org_id, created_at);

CREATE TABLE relay_reply_opportunity_targets (
  opportunity_id  INTEGER NOT NULL REFERENCES relay_reply_opportunities(id),
  member_id       INTEGER NOT NULL REFERENCES relay_members(id),
  PRIMARY KEY (opportunity_id, member_id)
);

CREATE TABLE relay_reply_notifications (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  opportunity_id  INTEGER NOT NULL REFERENCES relay_reply_opportunities(id),
  member_id       INTEGER NOT NULL REFERENCES relay_members(id),
  notified_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  dismissed_at    TEXT,
  replied_at      TEXT,
  replied_tweet_id TEXT
);
CREATE UNIQUE INDEX relay_reply_notifications_unique
  ON relay_reply_notifications(opportunity_id, member_id);
CREATE INDEX relay_reply_notifications_inbox
  ON relay_reply_notifications(member_id, dismissed_at);

CREATE TABLE relay_processed_updates (
  platform        TEXT NOT NULL CHECK (platform IN ('telegram','discord')),
  update_id       TEXT NOT NULL,
  processed_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (platform, update_id)
);
CREATE INDEX relay_processed_updates_gc
  ON relay_processed_updates(processed_at);

UPDATE schema_version SET version = 57 WHERE version < 57;
