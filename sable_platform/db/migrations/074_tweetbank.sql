-- 074_tweetbank.sql
-- Tweet Assist Compose -- the TWEETBANK (P3 human-fed + P4 AI-suggested). A curated
-- store of ready-to-post original tweets, keyed per managed account (account_handle)
-- with a shared per-org GLOBAL pool (account_handle IS NULL). An operator's view =
-- their granted accounts UNION global, dial/topic-filtered (copypastas merged in the
-- app layer for the shitpost slice). Humans submit -> status='approved' directly. The
-- P4 AI suggester writes source='ai' status='pending' for an approver to clear.
--
-- 100 percent ADDITIVE: CREATE TABLE IF NOT EXISTS + CREATE INDEX only. NO rebuild,
-- NO column drop. Comment hygiene: no semicolons inside double-dash comment lines (the
-- runner splits on the literal semicolon). Conventions: counts/PKs INTEGER, all _at
-- columns TEXT with a strftime default, JSON blobs TEXT.
--
-- This is CONTENT, not a cost surface -- there is NO cost column, ever. FK -> orgs
-- (managed-account compose works for any client org, not only relay-enabled ones).
-- The 'used' status is an ADVISORY soft-claim (mark-used on Compose so an idea is not
-- double-posted across operators) -- not a hard lock (plan section 9).

CREATE TABLE IF NOT EXISTS tweetbank_entries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    client_org     TEXT NOT NULL REFERENCES orgs(org_id),
    account_handle TEXT,
    text           TEXT NOT NULL,
    register_band  TEXT,
    topic_tags     TEXT NOT NULL DEFAULT '[]',
    author         TEXT,
    source         TEXT NOT NULL DEFAULT 'human',
    status         TEXT NOT NULL DEFAULT 'approved',
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    used_at        TEXT,
    used_by        TEXT,
    CHECK (source IN ('human', 'ai')),
    CHECK (status IN ('approved', 'pending', 'used', 'rejected'))
);

CREATE INDEX IF NOT EXISTS ix_tweetbank_entries_org
  ON tweetbank_entries(client_org, status, account_handle);

UPDATE schema_version SET version = 74 WHERE version < 74;
