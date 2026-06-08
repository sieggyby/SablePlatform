-- 071_relay_topic_suggestions.sql
-- Tweet Assist Compose -- topic-suggestion engine (P2). A weekly refresh job
-- aggregates deterministic candidates per client org (trending stories + topic
-- signals + content drops + lexicon/narrative + the org brief), runs ONE batched
-- Claude pass to synthesize ranked suggested topics + angles, and CACHES the
-- result here so the compose UI reads topics at near-zero per-view cost. 100 percent
-- ADDITIVE: CREATE TABLE IF NOT EXISTS + CREATE INDEX only. NO table rebuild, NO
-- column drop.
--
-- Comment hygiene: no semicolons inside double-dash comment lines (the runner in
-- connection.py splits on the literal semicolon). Conventions: counts/PKs INTEGER,
-- all _at columns TEXT with a strftime default, JSON blobs TEXT.
--
-- topics_json is INTERPRETIVE (LLM synthesis over signals), NOT measured fact --
-- SableWeb renders it behind a caveat. There is NO cost column here, ever (cost
-- lives only in cost_events, tagged relay_compose.topics). ONE current row per org
-- -- the refresh does an application-level delete-then-insert (relay/db), so there
-- is NO UNIQUE constraint and a recurring refresh stays ONE row.

CREATE TABLE IF NOT EXISTS relay_topic_suggestions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id        TEXT NOT NULL REFERENCES relay_clients(org_id),
    topics_json   TEXT NOT NULL DEFAULT '[]',
    model         TEXT,
    refreshed_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_relay_topic_suggestions_org
  ON relay_topic_suggestions(org_id, refreshed_at);

UPDATE schema_version SET version = 71 WHERE version < 71;
