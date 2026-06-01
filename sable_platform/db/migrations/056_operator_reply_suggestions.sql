-- 056_operator_reply_suggestions.sql
-- Operator reply-suggestion feature (SableWeb /ops -> Slopper generation).
-- Three tables: a persistent per-operator-per-UTC-day generation quota
-- (50/day, raisable), a log of every generation, and the actual-post
-- mapping used to measure assisted-vs-organic reply lift.
-- See Sable_Slopper/docs/OPERATOR_REPLY_WEB_FEATURE.md.
--
-- Comment hygiene: no semicolons inside double-dash comment lines. The runner
-- in connection.py splits on the literal semicolon character.
-- Column conventions: counts are INTEGER (never INT), all _at columns are TEXT
-- with a strftime default (migration 053 contract), PK/FK targets TEXT.

CREATE TABLE IF NOT EXISTS operator_reply_quota (
    operator_handle TEXT NOT NULL,
    day_utc         TEXT NOT NULL,
    org_id          TEXT,
    count           INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (operator_handle, day_utc)
);

CREATE TABLE IF NOT EXISTS reply_suggestions (
    id              TEXT PRIMARY KEY,
    operator_handle TEXT NOT NULL,
    org_id          TEXT NOT NULL REFERENCES orgs(org_id),
    source_tweet_id TEXT NOT NULL,
    source_author   TEXT,
    source_text     TEXT,
    variants_json   TEXT NOT NULL DEFAULT '[]',
    model           TEXT,
    cost_usd        REAL,
    generated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS reply_outcomes (
    id                 TEXT PRIMARY KEY,
    suggestion_id      TEXT NOT NULL REFERENCES reply_suggestions(id),
    posted_tweet_id    TEXT NOT NULL,
    posted_at          TEXT,
    chosen_variant_idx INTEGER,
    was_edited         INTEGER NOT NULL DEFAULT 0,
    engagement_json    TEXT NOT NULL DEFAULT '{}',
    recorded_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_reply_suggestions_match ON reply_suggestions(operator_handle, source_tweet_id);
CREATE INDEX IF NOT EXISTS ix_reply_suggestions_org ON reply_suggestions(org_id, generated_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_reply_outcomes_match ON reply_outcomes(suggestion_id, posted_tweet_id);

UPDATE schema_version SET version = 56 WHERE version < 56;
