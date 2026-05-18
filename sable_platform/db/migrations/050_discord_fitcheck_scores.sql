-- Migration 050: discord_fitcheck_scores for Scored Mode V2 Pass B.
-- One row per scored fit (success or failure -- score_status discriminates).
-- UNIQUE(guild_id, post_id) so re-scoring the same post upserts. percentile is
-- frozen at score time per design sec 2 (no re-curving). reveal_* columns are
-- populated by Pass C (out of scope for this PR) but the columns ship now so
-- the schema doesn't churn between PRs.
--
-- Comment-hygiene reminder: no semicolons inside double-dash comment lines.
-- The runner in connection.py splits on the literal semicolon character.

CREATE TABLE IF NOT EXISTS discord_fitcheck_scores (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id                   TEXT NOT NULL,
    guild_id                 TEXT NOT NULL,
    post_id                  TEXT NOT NULL,
    user_id                  TEXT NOT NULL,
    posted_at                TEXT NOT NULL,
    scored_at                TEXT NOT NULL,
    model_id                 TEXT NOT NULL,
    prompt_version           TEXT NOT NULL,
    score_status             TEXT NOT NULL,
    score_error              TEXT,
    axis_cohesion            INTEGER,
    axis_execution           INTEGER,
    axis_concept             INTEGER,
    axis_catch               INTEGER,
    raw_total                INTEGER,
    catch_detected           TEXT,
    catch_naming_class       TEXT,
    description              TEXT,
    confidence               REAL,
    axis_rationales_json     TEXT,
    curve_basis              TEXT,
    pool_size_at_score_time  INTEGER,
    percentile               REAL,
    reveal_eligible          INTEGER NOT NULL DEFAULT 0,
    reveal_fired_at          TEXT,
    reveal_post_id           TEXT,
    reveal_trigger           TEXT,
    invalidated_at           TEXT,
    invalidated_reason       TEXT,
    created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (guild_id, post_id)
);

CREATE INDEX IF NOT EXISTS idx_discord_fitcheck_scores_user_pct
    ON discord_fitcheck_scores (org_id, user_id, percentile DESC);
CREATE INDEX IF NOT EXISTS idx_discord_fitcheck_scores_org_posted
    ON discord_fitcheck_scores (org_id, posted_at);
CREATE INDEX IF NOT EXISTS idx_discord_fitcheck_scores_status
    ON discord_fitcheck_scores (org_id, score_status);
CREATE INDEX IF NOT EXISTS idx_discord_fitcheck_scores_reveal_fired
    ON discord_fitcheck_scores (org_id, reveal_fired_at);

UPDATE schema_version SET version = 50 WHERE version < 50;
