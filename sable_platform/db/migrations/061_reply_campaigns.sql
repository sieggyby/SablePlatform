-- 061_reply_campaigns.sql
-- Coordinated reply campaigns (the "flash mob"): several operators reply to ONE
-- target tweet toward a shared objective, with de-duped angles + outcome tracking.
-- Two tables -- the campaign (target + objective + status) and per-operator
-- assignments (who took which angle, what they posted). Ties into the existing
-- reply_suggestions / reply_outcomes (migration 056).
--
-- Comment hygiene: no semicolons inside double-dash comment lines (the runner in
-- connection.py splits on the literal semicolon). Column conventions: counts
-- INTEGER, all _at columns TEXT with a strftime default, PK/FK targets TEXT.

CREATE TABLE IF NOT EXISTS reply_campaigns (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES orgs(org_id),
    target_tweet_id TEXT NOT NULL,
    target_url      TEXT,
    target_author   TEXT,
    objective       TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    created_by      TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    won_at          TEXT,
    closed_at       TEXT
);

CREATE TABLE IF NOT EXISTS reply_campaign_assignments (
    id              TEXT PRIMARY KEY,
    campaign_id     TEXT NOT NULL REFERENCES reply_campaigns(id),
    operator_handle TEXT NOT NULL,
    suggestion_id   TEXT,
    posted_tweet_id TEXT,
    angle           TEXT,
    status          TEXT NOT NULL DEFAULT 'assigned',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    posted_at       TEXT
);

CREATE INDEX IF NOT EXISTS ix_reply_campaigns_org ON reply_campaigns(org_id, status, created_at);
CREATE INDEX IF NOT EXISTS ix_reply_campaign_assignments_campaign ON reply_campaign_assignments(campaign_id);

UPDATE schema_version SET version = 61 WHERE version < 61;
