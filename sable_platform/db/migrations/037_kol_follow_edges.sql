-- Migration 037: SableKOL follow-graph extraction tables.
--
-- Two tables:
--   kol_extract_runs     — parent run record. One row per
--                          (target_handle, provider, extract_type) attempt.
--                          cursor_completed flag distinguishes complete graphs
--                          from partial extractions, so downstream clustering
--                          and kingmaker queries can filter to clean runs.
--   kol_follow_edges     — one row per (follower, followed) pair, tagged with
--                          the run_id that produced it.
--
-- Together these support the SolStitch outreach plan follow-graph extraction
-- (see ~/Projects/SableKOL/PLAN.md and the SolStitch outreach plan).
--
-- Why two tables (not one): partial extractions look identical to complete
-- ones in a single-table design and contaminate kingmaker counts. Splitting
-- the run record out lets analysis joins gate on cursor_completed=1.

CREATE TABLE IF NOT EXISTS kol_extract_runs (
    run_id                   TEXT PRIMARY KEY,
    target_handle_normalized TEXT NOT NULL,
    target_user_id           TEXT,
    provider                 TEXT NOT NULL,
    extract_type             TEXT NOT NULL,
    started_at               TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at             TEXT,
    cursor_completed         INTEGER NOT NULL DEFAULT 0,
    last_cursor              TEXT,
    pages_fetched            INTEGER NOT NULL DEFAULT 0,
    rows_inserted            INTEGER NOT NULL DEFAULT 0,
    expected_count           INTEGER,
    partial_failure_reason   TEXT,
    cost_usd_logged          REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_kol_extract_runs_target
    ON kol_extract_runs(target_handle_normalized, extract_type);

CREATE INDEX IF NOT EXISTS idx_kol_extract_runs_completed
    ON kol_extract_runs(cursor_completed);

CREATE TABLE IF NOT EXISTS kol_follow_edges (
    run_id           TEXT NOT NULL,
    follower_id      TEXT NOT NULL,
    follower_handle  TEXT,
    followed_id      TEXT NOT NULL,
    followed_handle  TEXT NOT NULL,
    fetched_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (run_id, follower_id, followed_id),
    FOREIGN KEY (run_id) REFERENCES kol_extract_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_kol_follow_edges_followed
    ON kol_follow_edges(followed_id);

CREATE INDEX IF NOT EXISTS idx_kol_follow_edges_followed_handle
    ON kol_follow_edges(followed_handle);

CREATE INDEX IF NOT EXISTS idx_kol_follow_edges_follower
    ON kol_follow_edges(follower_id);

UPDATE schema_version SET version = 37 WHERE version < 37;
