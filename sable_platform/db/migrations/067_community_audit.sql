-- 067_community_audit.sql
-- Community-audit bot (sable-audit). Backs a self-invite Discord bot that audits
-- a server (settings/structure/roster + a consent-gated message-history crawl) and
-- produces a plain-language grade + a quality-weighted contributor leaderboard.
-- SP owns these tables. sable-audit is a thin client importing sable_platform.db.
--
-- Naming: the community_audit_ prefix deliberately avoids the existing audit_log
-- table / db/audit.py (compliance audit LOG) -- a DIFFERENT surface. Never name a
-- community-audit table audit_*.
--
-- Comment hygiene: no semicolons inside double-dash comment lines (the runner in
-- connection.py splits on the literal semicolon). Column conventions (migration 053
-- contract): counts/PKs INTEGER (never INT), scores REAL, all _at columns TEXT with
-- the strftime ISO-8601-Z default below (NOT datetime('now'), which emits a
-- space-separated no-Z format), JSON blobs TEXT.

-- Parent: one row per guild the bot has joined. org_id is NULL until consent
-- (the prospect org is created at consent, not join -- a drive-by invite that never
-- consents leaves no orgs row). Re-invite reuses the row (guild_id PK).
CREATE TABLE community_audit_guilds (
  guild_id        TEXT PRIMARY KEY,
  org_id          TEXT REFERENCES orgs(org_id),
  invited_by      TEXT,
  plan_tier       TEXT NOT NULL DEFAULT 'free',
  status          TEXT NOT NULL DEFAULT 'active',
  consent_at      TEXT,
  joined_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  last_audit_at   TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- One row per audit run (metadata = instant on-join, deep = consent-gated crawl).
-- overall_grade is NULL until the grade-suppression rule is satisfied.
CREATE TABLE community_audit_runs (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id            TEXT NOT NULL REFERENCES community_audit_guilds(guild_id),
  tier                TEXT NOT NULL DEFAULT 'free',
  kind                TEXT NOT NULL CHECK (kind IN ('metadata','deep')),
  status              TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','ok','aborted','partial')),
  messages_analyzed   INTEGER NOT NULL DEFAULT 0,
  channels_active     INTEGER NOT NULL DEFAULT 0,
  channels_dead       INTEGER NOT NULL DEFAULT 0,
  span_start          TEXT,
  overall_grade       TEXT,
  category_grades_json TEXT NOT NULL DEFAULT '{}',
  started_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  finished_at         TEXT,
  created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX community_audit_runs_by_guild
  ON community_audit_runs(guild_id, started_at);

-- Plain-language findings, each with a jump-link (message_ref) for click-to-verify.
-- NO verbatim message snippet in the free tier (R4): message_ref is a link, not text.
CREATE TABLE community_audit_findings (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        INTEGER NOT NULL REFERENCES community_audit_runs(id),
  category      TEXT NOT NULL,
  severity      TEXT NOT NULL DEFAULT 'info',
  type          TEXT NOT NULL,
  title         TEXT NOT NULL,
  plain_detail  TEXT,
  message_ref   TEXT,
  confidence    REAL,
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX community_audit_findings_by_run
  ON community_audit_findings(run_id, category);

-- Deterministic security checklist results (pass/warn/fail per check_key).
CREATE TABLE community_audit_security_checks (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      INTEGER NOT NULL REFERENCES community_audit_runs(id),
  check_key   TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('pass','warn','fail')),
  detail      TEXT,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX community_audit_security_checks_by_run
  ON community_audit_security_checks(run_id);

-- Identity & Polish snapshot (boosts/emojis/soundboard/vanity/verification). One
-- snapshot per run.
CREATE TABLE community_audit_settings_snapshot (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id              INTEGER NOT NULL REFERENCES community_audit_runs(id),
  boost_level         INTEGER NOT NULL DEFAULT 0,
  boost_count         INTEGER NOT NULL DEFAULT 0,
  custom_emoji_count  INTEGER NOT NULL DEFAULT 0,
  soundboard_count    INTEGER NOT NULL DEFAULT 0,
  vanity_url          TEXT,
  has_banner          INTEGER NOT NULL DEFAULT 0,
  has_icon            INTEGER NOT NULL DEFAULT 0,
  verification_level  TEXT,
  description         TEXT,
  raw_json            TEXT NOT NULL DEFAULT '{}',
  created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE UNIQUE INDEX community_audit_settings_snapshot_by_run
  ON community_audit_settings_snapshot(run_id);

-- Reaction-existence ledger: one row = "this reaction currently exists." ADD upserts
-- the row, REMOVE deletes it (both idempotent). The leaderboard score is DERIVED by
-- COUNT over live rows -- so reaction removal correctly decrements (R3-N2).
CREATE TABLE community_audit_reaction_ledger (
  guild_id    TEXT NOT NULL REFERENCES community_audit_guilds(guild_id),
  post_id     TEXT NOT NULL,
  reactor_id  TEXT NOT NULL,
  emoji       TEXT NOT NULL,
  author_id   TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (guild_id, post_id, reactor_id, emoji)
);
CREATE INDEX community_audit_reaction_ledger_by_author
  ON community_audit_reaction_ledger(guild_id, author_id);

-- Materialized contributor score (derived from the ledger + thread-depth signals,
-- always recomputable -- never an authoritative monotonic counter).
CREATE TABLE community_audit_member_scores (
  guild_id          TEXT NOT NULL REFERENCES community_audit_guilds(guild_id),
  member_id         TEXT NOT NULL,
  contribution_score REAL NOT NULL DEFAULT 0,
  components_json   TEXT NOT NULL DEFAULT '{}',
  last_active_at    TEXT,
  updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (guild_id, member_id)
);
CREATE INDEX community_audit_member_scores_rank
  ON community_audit_member_scores(guild_id, contribution_score);

-- Per-member per-period activity, for the dormant-member reactivation list
-- (was-active-then-quiet requires a historical snapshot, not a mutable flag).
CREATE TABLE community_audit_member_activity (
  guild_id      TEXT NOT NULL REFERENCES community_audit_guilds(guild_id),
  member_id     TEXT NOT NULL,
  period        TEXT NOT NULL,
  message_count INTEGER NOT NULL DEFAULT 0,
  updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (guild_id, member_id, period)
);

-- Rate-limit + cost counters backing the concrete per-guild / per-inviter / global
-- limits (defeats throwaway-server abuse the per-guild limit cannot bound alone).
CREATE TABLE community_audit_rate_limits (
  scope         TEXT NOT NULL CHECK (scope IN ('guild','inviter','global')),
  key           TEXT NOT NULL,
  window_start  TEXT NOT NULL,
  count         INTEGER NOT NULL DEFAULT 0,
  ai_usd        REAL NOT NULL DEFAULT 0,
  updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (scope, key, window_start)
);

-- Anonymized cross-server per-category score distribution, for the "vs median" band.
CREATE TABLE community_audit_benchmark (
  category          TEXT NOT NULL,
  metric_key        TEXT NOT NULL,
  distribution_json TEXT NOT NULL DEFAULT '{}',
  sample_size       INTEGER NOT NULL DEFAULT 0,
  updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (category, metric_key)
);

-- Twitter<->Discord identity link, for the paid blended leaderboard (empty in v1 --
-- the blend is gated on populating this).
CREATE TABLE community_audit_identity_links (
  guild_id          TEXT NOT NULL REFERENCES community_audit_guilds(guild_id),
  discord_member_id TEXT NOT NULL,
  twitter_handle    TEXT NOT NULL,
  confidence        REAL,
  source            TEXT,
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (guild_id, discord_member_id)
);

UPDATE schema_version SET version = 67 WHERE version < 67;
