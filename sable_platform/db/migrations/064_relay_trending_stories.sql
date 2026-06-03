-- 064_relay_trending_stories.sql
-- Trending-Story Autopilot (reply-assist x SableRelay). Stage A detects bursting
-- AND relevant stories from the scored sweep pool and persists them here, Stage B
-- auto-monitors them via decaying relay_sweep_config.topic_queries, and Stage C
-- reads this table for the sable.tools "Trending Stories" digest. 100 percent
-- ADDITIVE: CREATE TABLE IF NOT EXISTS + CREATE INDEX only. NO table rebuild, NO
-- column drop. See SableRelay/TRENDING_STORY_AUTOPILOT_PLAN.md.
--
-- Comment hygiene: no semicolons inside double-dash comment lines (the runner in
-- connection.py splits on the literal semicolon). Conventions: counts/PKs INTEGER,
-- scores REAL, all _at columns TEXT with a strftime default, JSON blobs TEXT.
--
-- relevance/momentum/summary are INTERPRETIVE (cluster/scorer judgement + a
-- derived volume trend), NOT measured fact -- SableWeb renders them behind a
-- caveat banner. There is NO cost column here, ever (cost lives only in
-- cost_events). Dedup is application-level (relay/db.upsert_trending_story) -- a
-- story recurring across sweeps is ONE row, so NO UNIQUE constraint here.

CREATE TABLE IF NOT EXISTS relay_trending_stories (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id                TEXT NOT NULL REFERENCES relay_clients(org_id),
    label                 TEXT NOT NULL,
    summary               TEXT,
    relevance             REAL,
    momentum              REAL,
    member_tweet_ids_json TEXT NOT NULL DEFAULT '[]',
    monitor_terms_json    TEXT NOT NULL DEFAULT '[]',
    status                TEXT NOT NULL DEFAULT 'emerging',
    first_seen_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_seen_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at            TEXT,
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_relay_trending_stories_feed
  ON relay_trending_stories(org_id, status);

UPDATE schema_version SET version = 64 WHERE version < 64;
