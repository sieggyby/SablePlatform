-- 065_relay_quality_corpus.sql
-- Tweet-quality corpus store (SableRelay). A curated, stratified bank of CT
-- accounts (relay_quality_accounts) + the tweets we sample from them
-- (relay_quality_tweets) + a longitudinal engagement-decay log per tweet
-- (relay_tweet_snapshots, repeated at target ages so we learn the
-- like/retweet/view trajectory). Feeds the quality model that judges and
-- improves reply-assist drafts. 100 percent ADDITIVE: CREATE TABLE IF NOT
-- EXISTS + CREATE INDEX only. NO table rebuild, NO column drop. See
-- SableRelay/QUALITY_CORPUS_PLAN.md and scripts/seed_general_ct.py.
--
-- Comment hygiene: no semicolons inside double-dash comment lines (the runner in
-- connection.py splits on the literal semicolon). Conventions: counts/PKs INTEGER,
-- scores REAL, all _at columns TEXT with a strftime default, JSON blobs TEXT.
--
-- band/kol_strength/archetype_json on relay_quality_accounts are INTERPRETIVE
-- (KOL-list judgement carried over from kol_candidates), NOT measured fact.
-- relay_tweet_snapshots rows ARE measured (SocialData metrics at a known age) --
-- target_age_hours is the scheduled bucket, age_hours the actual age at capture.
-- There is NO cost column here, ever (cost lives only in cost_events). Accounts
-- are keyed by handle and tweets by their X id -- re-seeing one is ONE row, so
-- NO surrogate id and NO UNIQUE constraint beyond the natural primary key.

CREATE TABLE IF NOT EXISTS relay_quality_accounts (
    handle             TEXT PRIMARY KEY,
    band               TEXT,
    kol_strength       REAL,
    archetype_json     TEXT NOT NULL DEFAULT '[]',
    source             TEXT,
    followers_snapshot INTEGER,
    active             INTEGER NOT NULL DEFAULT 1,
    added_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS relay_quality_tweets (
    tweet_x_id     TEXT PRIMARY KEY,
    author_handle  TEXT,
    posted_at      TEXT,
    text           TEXT,
    band           TEXT,
    first_seen_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS relay_tweet_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    tweet_x_id       TEXT NOT NULL,
    target_age_hours INTEGER NOT NULL,
    taken_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    age_hours        REAL,
    likes            INTEGER,
    retweets         INTEGER,
    replies          INTEGER,
    quotes           INTEGER,
    bookmarks        INTEGER,
    views            INTEGER,
    author_followers INTEGER,
    status           TEXT NOT NULL DEFAULT 'ok'
);

CREATE INDEX IF NOT EXISTS ix_relay_tweet_snapshots_tweet ON relay_tweet_snapshots(tweet_x_id);
CREATE INDEX IF NOT EXISTS ix_relay_quality_tweets_posted ON relay_quality_tweets(posted_at);

UPDATE schema_version SET version = 65 WHERE version < 65;
