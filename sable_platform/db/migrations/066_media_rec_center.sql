-- 066_media_rec_center.sql
-- Media recommendation center (SableRelay / reply-assist). Records each
-- media "slate" we offered an operator for a reply (media_rec_events) + the
-- learned per-asset quality from those choices (media_quality, an Elo updated
-- incrementally as chosen-beats-the-rest pairwise outcomes) + a cached
-- semantic embedding per asset (media_embeddings, for similarity recall). Also
-- stamps the chosen media back onto the reply outcome (reply_outcomes
-- ADD COLUMN media_content_id) so assisted-vs-organic lift can be sliced by the
-- media that rode along. 100 percent ADDITIVE: one ALTER TABLE ADD COLUMN +
-- CREATE TABLE IF NOT EXISTS + CREATE INDEX only. NO table rebuild, NO column
-- drop. See SableRelay/MEDIA_REC_CENTER_PLAN.md.
--
-- Comment hygiene: no semicolons inside double-dash comment lines (the runner in
-- connection.py splits on the literal semicolon). Conventions: counts/PKs INTEGER,
-- scores REAL, all _at columns TEXT with a strftime default, JSON blobs TEXT.
--
-- elo/n_offered/n_chosen on media_quality are DERIVED from the choice log
-- (media_rec_events), recomputed forward-only by apply_pending_media_events --
-- the events table is the source of truth, media_quality is the materialized
-- rollup. chosen_content_id MAY be NULL (the operator was offered a slate but
-- attached no media). slate_json is the ordered list of offered content_ids.
-- media_embeddings is a per-asset cache (embedding_json + producing model) --
-- assets are keyed by (org_id, content_id) so the same content under two orgs
-- is two rows. There is NO cost column here, ever (cost lives only in
-- cost_events).

ALTER TABLE reply_outcomes ADD COLUMN media_content_id TEXT;

CREATE TABLE IF NOT EXISTS media_rec_events (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id             TEXT NOT NULL,
    operator_handle    TEXT,
    tweet_ref          TEXT,
    slate_json         TEXT NOT NULL DEFAULT '[]',
    chosen_content_id  TEXT,
    applied            INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS media_quality (
    org_id      TEXT NOT NULL,
    content_id  TEXT NOT NULL,
    elo         REAL NOT NULL DEFAULT 1500,
    n_offered   INTEGER NOT NULL DEFAULT 0,
    n_chosen    INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (org_id, content_id)
);

CREATE TABLE IF NOT EXISTS media_embeddings (
    org_id          TEXT NOT NULL,
    content_id      TEXT NOT NULL,
    embedding_json  TEXT,
    embedding_model TEXT,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (org_id, content_id)
);

CREATE INDEX IF NOT EXISTS ix_media_rec_events_unapplied ON media_rec_events(org_id, applied);

UPDATE schema_version SET version = 66 WHERE version < 66;
