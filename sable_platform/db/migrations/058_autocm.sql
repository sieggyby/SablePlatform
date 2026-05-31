-- 058_autocm.sql
-- SableAutoCM schema (the autocm_* table family) -- the per-client AI community
-- manager (persona = NULO for RobotMoney). Built as a SablePlatform module
-- (sable_platform.autocm), tables prefixed autocm_* to namespace cleanly
-- alongside relay_*, kol_*, discord_*, org_* tables.
--
-- Source of truth: SableAutoCM/DESIGN.md section 4 (the 11 base tables) +
-- KB_DESIGN.md (kb storage + retrieval) + DIGEST.md section 4 (founder digest
-- interactions) + sable-pulse/MEGAPLAN.md C3.0 (the single-logical-change
-- additions). Three sets of additions beyond DESIGN section 4's 11 base tables,
-- citation-pinned for traceability:
--
--   1. autocm_digest_interactions (DIGEST section 4) -- founder digest button
--      responses (Approve-for-KB / Recognize / Demote / Compose / Ignore / Ask)
--      captured for weekly review. Folding it in here avoids a second AutoCM
--      migration.
--   2. autocm_clients.incident_active (MEGAPLAN C3.8b) -- per-client incident-mode
--      state flag.
--   3. autocm_category_state freeze columns (MEGAPLAN C3.8a / C3.5a) -- the
--      per-client/per-category SAFETY section 6 48h pure-HITL freeze
--      (freeze_until + freeze_reason + frozen_by).
--
-- Plus a per-client time-saved baseline table autocm_time_saved_baseline
-- (DIGEST section 2a/section 3 -- RESOLVED to YES): the headline time-saved leg
-- needs a calibrated baseline at engagement start per client (minutes_per_auto /
-- minutes_per_hitl + engagement-start), consumed by the C3.7 digest formula.
-- Including it here keeps 058 a single logical change so the digest fixture
-- expected values are deterministic.
--
-- DECISION D-2 (locked): embedding storage. autocm_kb_chunks.chunk_embedding is
-- TEXT (a JSON-encoded float array) -- pure SQLite, no extension. Retrieval does
-- app-side cosine top-K (the universal default + SQLite-dev path). A companion
-- FTS5 virtual table autocm_kb_chunks_fts over chunk_text provides the hybrid
-- keyword/BM25 leg (C3.2a) using the stdlib sqlite3 FTS5 module -- NO
-- enable_load_extension, NO sqlite-vss. pgvector is an OPTIONAL accelerator on
-- the shared SP Postgres (CREATE EXTENSION vector), gated behind an explicit ops
-- step -- the embedding column is the one intentional dialect divergence
-- (Postgres may use a vector type while SQLite keeps TEXT + app-side cosine).
-- There is
-- no separate SableRelay Postgres -- Relay folds into SABLE_DATABASE_URL.
--
-- org PK is orgs(org_id) (TEXT), not orgs.id. autocm_clients.org_id is a TEXT FK
-- to orgs(org_id). autocm_drafts source FKs point at the relay_* surface landed
-- in 057: source_message_id -> relay_messages(id), source_chat_id ->
-- relay_chats(id) (the C1.1 AutoCM->Relay FK reconciliation).
--
-- Comment hygiene: NO semicolons inside double-dash comment lines. The runner in
-- connection.py splits on the literal semicolon character, so a comment-semicolon
-- creates a phantom SQL statement that breaks init.
-- Column conventions (migration 053 contract): counts are INTEGER (never INT),
-- all _at columns are TEXT with the strftime ISO-8601-Z default below (NOT
-- datetime('now'), which emits a space-separated no-Z format).

CREATE TABLE autocm_personas (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  name               TEXT NOT NULL,
  description        TEXT,
  calm_prompt        TEXT,
  reactive_prompt    TEXT,
  calibration_set    TEXT NOT NULL DEFAULT '{}',
  config             TEXT NOT NULL DEFAULT '{}',
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE UNIQUE INDEX autocm_personas_name_unique
  ON autocm_personas(name);

CREATE TABLE autocm_clients (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id             TEXT NOT NULL REFERENCES orgs(org_id),
  persona_id         INTEGER REFERENCES autocm_personas(id),
  display_name       TEXT,
  autonomy_state     TEXT NOT NULL DEFAULT 'hitl'
                     CHECK (autonomy_state IN ('hitl','partial','auto','paused')),
  incident_active    INTEGER NOT NULL DEFAULT 0,
  surface_config     TEXT NOT NULL DEFAULT '{}',
  kb_config          TEXT NOT NULL DEFAULT '{}',
  enabled            INTEGER NOT NULL DEFAULT 0,
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE UNIQUE INDEX autocm_clients_org_unique
  ON autocm_clients(org_id);
CREATE INDEX autocm_clients_persona
  ON autocm_clients(persona_id);

CREATE TABLE autocm_kb_sources (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id          INTEGER NOT NULL REFERENCES autocm_clients(id),
  source_type        TEXT NOT NULL,
  source_url         TEXT,
  refresh_cadence    TEXT,
  authority_default  REAL NOT NULL DEFAULT 0.5,
  fetch_config       TEXT NOT NULL DEFAULT '{}',
  status             TEXT NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active','stale','disabled')),
  last_refreshed_at  TEXT,
  last_changed_at    TEXT,
  last_error         TEXT,
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX autocm_kb_sources_by_client
  ON autocm_kb_sources(client_id, source_type);
CREATE INDEX autocm_kb_sources_refresh
  ON autocm_kb_sources(status, last_refreshed_at);

-- chunk_embedding: TEXT (JSON-encoded float vector) per DECISION D-2. App-side
-- cosine top-K. The companion FTS5 virtual table below provides the keyword leg.
CREATE TABLE autocm_kb_chunks (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id          INTEGER NOT NULL REFERENCES autocm_kb_sources(id),
  client_id          INTEGER NOT NULL REFERENCES autocm_clients(id),
  chunk_text         TEXT NOT NULL,
  chunk_embedding    TEXT,
  chunk_metadata     TEXT NOT NULL DEFAULT '{}',
  chunk_authority    REAL NOT NULL DEFAULT 0.5,
  content_hash       TEXT,
  status             TEXT NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active','stale','wrong')),
  indexed_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX autocm_kb_chunks_by_source
  ON autocm_kb_chunks(source_id);
CREATE INDEX autocm_kb_chunks_by_client_status
  ON autocm_kb_chunks(client_id, status);

-- FTS5 companion index over chunk_text for the C3.2a hybrid keyword/BM25 leg.
-- stdlib sqlite3 FTS5 module -- no enable_load_extension. content-linked to
-- autocm_kb_chunks via content rowid (external-content FTS5). Single statement
-- (no internal semicolon) so the connection.py semicolon-splitter executes it
-- whole.
CREATE VIRTUAL TABLE autocm_kb_chunks_fts USING fts5(
  chunk_text,
  content='autocm_kb_chunks',
  content_rowid='id'
);

-- slot-fill registry (KB_DESIGN section 2). Composite TEXT key (client_id, key).
-- irreducibles -- contract addresses, audit URLs -- NEVER LLM-generated. EXCLUDED
-- from SEQUENCE_TABLES (composite/TEXT PK, no autoincrement).
CREATE TABLE autocm_kb_constants (
  client_id          INTEGER NOT NULL REFERENCES autocm_clients(id),
  key                TEXT NOT NULL,
  value              TEXT NOT NULL,
  description        TEXT,
  updated_by         TEXT,
  updated_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (client_id, key)
);

-- autocm_drafts: every draft with source, classification, register, confidence,
-- KB sources cited, status. source FKs point at the relay_* surface (057):
-- source_message_id -> relay_messages(id), source_chat_id -> relay_chats(id).
CREATE TABLE autocm_drafts (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id          INTEGER NOT NULL REFERENCES autocm_clients(id),
  source_message_id  INTEGER REFERENCES relay_messages(id),
  source_chat_id     INTEGER REFERENCES relay_chats(id),
  category           TEXT,
  tier               INTEGER,
  register           TEXT CHECK (register IN ('calm','reactive')),
  draft_text         TEXT,
  confidence         REAL,
  cited_chunk_ids    TEXT NOT NULL DEFAULT '[]',
  status             TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','auto_sent','hitl_pending','approved','rejected','published','escalated','suppressed')),
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  resolved_at        TEXT
);
CREATE INDEX autocm_drafts_by_client_status
  ON autocm_drafts(client_id, status, created_at);
CREATE INDEX autocm_drafts_by_category
  ON autocm_drafts(client_id, category, created_at);
CREATE INDEX autocm_drafts_by_message
  ON autocm_drafts(source_message_id);

CREATE TABLE autocm_reviews (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id           INTEGER NOT NULL REFERENCES autocm_drafts(id),
  client_id          INTEGER NOT NULL REFERENCES autocm_clients(id),
  reviewer           TEXT,
  decision           TEXT NOT NULL
                     CHECK (decision IN ('approve','edit','reject','punt_to_founder')),
  edited_text        TEXT,
  edit_diff_size     REAL NOT NULL DEFAULT 0,
  is_clean_approval  INTEGER NOT NULL DEFAULT 0,
  note               TEXT,
  reviewed_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX autocm_reviews_by_draft
  ON autocm_reviews(draft_id);
CREATE INDEX autocm_reviews_by_client
  ON autocm_reviews(client_id, reviewed_at);

-- per-client x per-category autonomy state + threshold + sample count. The
-- freeze_until / freeze_reason / frozen_by columns implement the SAFETY section 6
-- 48h pure-HITL freeze (MEGAPLAN C3.8a / C3.5a).
CREATE TABLE autocm_category_state (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id            INTEGER NOT NULL REFERENCES autocm_clients(id),
  category             TEXT NOT NULL,
  state                TEXT NOT NULL DEFAULT 'hitl'
                       CHECK (state IN ('hitl','auto')),
  confidence_threshold REAL NOT NULL DEFAULT 0.8,
  sample_count         INTEGER NOT NULL DEFAULT 0,
  clean_approval_count INTEGER NOT NULL DEFAULT 0,
  freeze_until         TEXT,
  freeze_reason        TEXT,
  frozen_by            TEXT,
  updated_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE UNIQUE INDEX autocm_category_state_unique
  ON autocm_category_state(client_id, category);
CREATE INDEX autocm_category_state_frozen
  ON autocm_category_state(freeze_until);

CREATE TABLE autocm_escalations (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id          INTEGER NOT NULL REFERENCES autocm_clients(id),
  draft_id           INTEGER REFERENCES autocm_drafts(id),
  source_message_id  INTEGER REFERENCES relay_messages(id),
  reason             TEXT,
  founder_status     TEXT NOT NULL DEFAULT 'pending'
                     CHECK (founder_status IN ('pending','notified','acknowledged','resolved')),
  oncall_status      TEXT NOT NULL DEFAULT 'pending'
                     CHECK (oncall_status IN ('pending','notified','acknowledged','resolved')),
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  resolved_at        TEXT
);
CREATE INDEX autocm_escalations_by_client
  ON autocm_escalations(client_id, created_at);
CREATE INDEX autocm_escalations_open
  ON autocm_escalations(founder_status, oncall_status);

-- users currently auto-silenced pending mod clearance.
CREATE TABLE autocm_flagged_users (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id          INTEGER NOT NULL REFERENCES autocm_clients(id),
  member_id          INTEGER REFERENCES relay_members(id),
  external_user_id   TEXT,
  reason             TEXT,
  status             TEXT NOT NULL DEFAULT 'silenced'
                     CHECK (status IN ('silenced','cleared')),
  flagged_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  cleared_at         TEXT,
  cleared_by         TEXT
);
CREATE INDEX autocm_flagged_users_by_client
  ON autocm_flagged_users(client_id, status);
CREATE INDEX autocm_flagged_users_by_member
  ON autocm_flagged_users(member_id);

-- daily adversarial regression test results.
CREATE TABLE autocm_adversarial_runs (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id          INTEGER NOT NULL REFERENCES autocm_clients(id),
  suite              TEXT,
  total_cases        INTEGER NOT NULL DEFAULT 0,
  passed             INTEGER NOT NULL DEFAULT 0,
  failed             INTEGER NOT NULL DEFAULT 0,
  result             TEXT NOT NULL DEFAULT '{}',
  status             TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','passed','failed','error')),
  ran_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX autocm_adversarial_runs_by_client
  ON autocm_adversarial_runs(client_id, ran_at);

-- founder digest button responses (DIGEST section 4) captured for weekly review:
-- Approve-for-KB / Recognize / Demote / Compose / Ignore / Ask.
CREATE TABLE autocm_digest_interactions (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id          INTEGER NOT NULL REFERENCES autocm_clients(id),
  digest_period      TEXT,
  section            TEXT,
  action             TEXT NOT NULL
                     CHECK (action IN ('approve_for_kb','recognize','demote','compose','ignore','ask')),
  target_ref         TEXT,
  payload            TEXT NOT NULL DEFAULT '{}',
  actor              TEXT,
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX autocm_digest_interactions_by_client
  ON autocm_digest_interactions(client_id, digest_period);
CREATE INDEX autocm_digest_interactions_by_action
  ON autocm_digest_interactions(client_id, action);

-- per-client time-saved baseline (DIGEST section 2a/section 3). minutes_per_auto
-- / minutes_per_hitl calibration + engagement-start baseline, consumed by the
-- C3.7 digest time-saved formula. One row per client (engagement-start anchor).
CREATE TABLE autocm_time_saved_baseline (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id            INTEGER NOT NULL REFERENCES autocm_clients(id),
  minutes_per_auto     REAL NOT NULL DEFAULT 0,
  minutes_per_hitl     REAL NOT NULL DEFAULT 0,
  engagement_start_at  TEXT,
  calibrated_by        TEXT,
  notes                TEXT,
  created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE UNIQUE INDEX autocm_time_saved_baseline_client_unique
  ON autocm_time_saved_baseline(client_id);

UPDATE schema_version SET version = 58 WHERE version < 58;
