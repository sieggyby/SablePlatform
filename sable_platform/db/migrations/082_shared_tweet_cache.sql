-- 082_shared_tweet_cache.sql
-- The shared SocialData cache (POSTED_REPLY_DETECTION_AND_SHARED_CACHE_PLAN.md steps 2-3):
-- relay_tweets becomes the cross-system tweet substrate that BOTH the reply stack AND
-- Cult Grader read-before-fetch and write-through, so the same tweet is never paid for
-- twice across systems.
--
-- (1) relay_search_windows -- Layer B, the CLOSED-WINDOW search cache. A date-bounded
--     search over a PAST window ("@client since:X until:Y" where Y is in the past) is
--     FINAL -- no new tweets can appear in a window that is over -- so the first system
--     to run it records the result-set (as x_ids resolved through relay_tweets) and the
--     second reuses it for $0. This lifts Cult Grader's per-project local JSON window
--     checkpoint into the shared DB. The CURRENT/OPEN window is never cached (the
--     helper refuses to mark a window whose end is in the future). result_ids_json is
--     the authoritative membership list -- reuse hydrates rows from relay_tweets and
--     treats ANY missing id as a cache miss (fail-open to a live fetch, never a
--     silently-partial result).
--
-- (2) relay_tweets.posted_at -- the tweet's REAL creation time (ISO-Z), stamped by
--     write-through from the SocialData payload. Load-bearing for the Layer-A PLATEAU
--     rule (engagement is effectively final >= 14 days after posting -> cached
--     engagement serves forever, younger tweets re-fetch) and the bidirectional-flow
--     routing gates (fresh <24h -> fixed-age corpus queue, <6h -> lateral opportunity).
--     Nullable -- pre-082 rows lack it (their raw payload still carries it, and
--     readers treat NULL as unknown -> not plateaued -> miss).
--
-- (3) relay_tweets.source + relay_tweet_snapshots.source -- provenance bookkeeping.
--     On snapshots, source is the K1 ANTI-CONTAMINATION marker: NULL/absent = the
--     fixed-age track (24h/72h EzS readings) while 'cult_final' = Cult Grader's
--     final-engagement-at-maturity track, written with target_age_hours = -1 so no
--     fixed-age consumer (which filters target_age_hours = 24/72) can ever pool it.
--     The two outcome tracks are trained on separately, never mixed (the K1 kill-test
--     failure mode).
--
-- 100% ADDITIVE (CREATE TABLE + ADD COLUMN + CREATE INDEX -- no rebuild, no drop).
-- Comment hygiene: NO semicolons inside double-dash comment lines (the runner splits on the char).

CREATE TABLE relay_search_windows (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  query_norm     TEXT NOT NULL,
  window_start   TEXT NOT NULL,
  window_end     TEXT NOT NULL,
  completed_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  result_count   INTEGER NOT NULL DEFAULT 0,
  result_ids_json TEXT NOT NULL DEFAULT '[]',
  source         TEXT,
  UNIQUE (query_norm, window_start, window_end)
);

CREATE INDEX ix_relay_search_windows_query ON relay_search_windows(query_norm, window_start);

ALTER TABLE relay_tweets ADD COLUMN posted_at TEXT;

ALTER TABLE relay_tweets ADD COLUMN source TEXT;

ALTER TABLE relay_tweet_snapshots ADD COLUMN source TEXT;

CREATE INDEX ix_relay_tweets_posted_at ON relay_tweets(posted_at);

UPDATE schema_version SET version = 82 WHERE version < 82;
