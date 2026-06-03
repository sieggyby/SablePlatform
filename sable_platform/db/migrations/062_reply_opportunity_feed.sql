-- 062_reply_opportunity_feed.sql
-- Reply-Opportunity Feed (reply-assist x SableRelay). Auto-sourced reply
-- opportunities surfaced as a per-operator feed in sable.tools /ops/reply-assist,
-- unified on the EXISTING relay_reply_opportunities table (migration 057) by
-- extending it -- NOT a parallel table. 100 percent ADDITIVE: ADD COLUMN +
-- CREATE TABLE IF NOT EXISTS + CREATE INDEX only. NO table rebuild (the runner
-- wraps each file in one transaction with foreign_keys ON, so a DROP TABLE would
-- raise on the FK-d child tables), NO CHECK change, NO NOT-NULL relax. See
-- SableRelay/REPLY_OPPORTUNITY_FEED_PLAN.md section 3.
--
-- Comment hygiene: no semicolons inside double-dash comment lines (the runner in
-- connection.py splits on the literal semicolon). Conventions: counts/PKs
-- INTEGER, all _at columns TEXT with a strftime default, FK targets as declared.

-- Extend the existing relay_reply_opportunities (057) for the feed. Auto-sourced
-- rows reuse an existing allowed origin value ('auto_mention' / 'explicit_command')
-- and carry the real source in sweep_source -- so the 057 origin CHECK passes
-- unchanged. flagger_id stays NOT NULL (the sweep uses a sentinel __sweep__
-- relay_members row). status backfills to 'active' on existing rows.
ALTER TABLE relay_reply_opportunities ADD COLUMN score REAL;
ALTER TABLE relay_reply_opportunities ADD COLUMN score_reason TEXT;
ALTER TABLE relay_reply_opportunities ADD COLUMN suggested_angle TEXT;
ALTER TABLE relay_reply_opportunities ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE relay_reply_opportunities ADD COLUMN expires_at TEXT;
ALTER TABLE relay_reply_opportunities ADD COLUMN sweep_source TEXT;

CREATE INDEX IF NOT EXISTS ix_relay_opportunities_feed
  ON relay_reply_opportunities(org_id, status, score);
CREATE INDEX IF NOT EXISTS ix_relay_opportunities_expiry
  ON relay_reply_opportunities(expires_at);

-- relay_tweets read-through cache: cheap quality signals for the heuristic pre-rank.
ALTER TABLE relay_tweets ADD COLUMN engagement_json TEXT;
ALTER TABLE relay_tweets ADD COLUMN lang TEXT;
ALTER TABLE relay_tweets ADD COLUMN author_followers INTEGER;

-- reply_suggestions (056): the learning join + the cheap local depress-already-replied.
ALTER TABLE reply_suggestions ADD COLUMN opportunity_id INTEGER;
ALTER TABLE reply_suggestions ADD COLUMN source_conversation_id TEXT;

-- Per-operator web-feed state (handle-keyed -- distinct from the TG member-keyed
-- relay_reply_notifications). dismiss/snooze personalizes the shared feed view.
CREATE TABLE IF NOT EXISTS relay_opportunity_operator_state (
    opportunity_id  INTEGER NOT NULL REFERENCES relay_reply_opportunities(id),
    operator_handle TEXT NOT NULL,
    state           TEXT NOT NULL,
    snooze_until    TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (opportunity_id, operator_handle)
);

-- The two thumbs (learning labels). suggestion_id NULL = thumb on the OPPORTUNITY
-- (relevance / ranker). suggestion_id set = thumb on a SUGGESTION (gen quality).
CREATE TABLE IF NOT EXISTS relay_opportunity_feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id  INTEGER NOT NULL REFERENCES relay_reply_opportunities(id),
    suggestion_id   TEXT REFERENCES reply_suggestions(id),
    rater_handle    TEXT NOT NULL,
    rater_role      TEXT NOT NULL,
    thumb           INTEGER NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_relay_opportunity_feedback_opp
  ON relay_opportunity_feedback(opportunity_id);

-- Per-client curated query set (managed via the TG bot command). The daily cost
-- cap is NOT here -- it lives in relay_clients.config.polling.daily_cost_cap_usd
-- (the existing get_daily_cost_cap resolver). last_sweep_at drives the hourly-due
-- check, sweep_requested_at is the "sweep now" enqueue marker (auto-consumed when
-- last_sweep_at is stamped at completion -- see plan section 4).
CREATE TABLE IF NOT EXISTS relay_sweep_config (
    org_id             TEXT PRIMARY KEY REFERENCES relay_clients(org_id),
    mention_handles    TEXT NOT NULL DEFAULT '[]',
    topic_queries      TEXT NOT NULL DEFAULT '[]',
    from_set           TEXT NOT NULL DEFAULT '[]',
    operator_handles   TEXT NOT NULL DEFAULT '[]',
    enabled            INTEGER NOT NULL DEFAULT 0,
    expiry_hours       INTEGER NOT NULL DEFAULT 36,
    last_sweep_at      TEXT,
    sweep_requested_at TEXT,
    updated_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Per-source since_id cursor (do NOT overload relay_clients.last_seen_x_id, which
-- is the broadcast-timeline poll cursor). query_hash distinguishes topic queries.
CREATE TABLE IF NOT EXISTS relay_sweep_cursor (
    org_id     TEXT NOT NULL,
    source     TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    since_id   TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (org_id, source, query_hash)
);

-- Logged-in gating: SableWeb stamps this on each /ops/reply-assist load. The sweep
-- only runs for orgs with a recent heartbeat.
CREATE TABLE IF NOT EXISTS relay_operator_heartbeat (
    org_id          TEXT NOT NULL,
    operator_handle TEXT NOT NULL,
    last_seen       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (org_id, operator_handle)
);

UPDATE schema_version SET version = 62 WHERE version < 62;
