-- Prospect scoring data from Lead Identifier
CREATE TABLE IF NOT EXISTS prospect_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id          TEXT NOT NULL,
    run_date        TEXT NOT NULL,
    composite_score REAL NOT NULL,
    tier            TEXT NOT NULL,
    stage           TEXT,
    dimensions_json TEXT NOT NULL DEFAULT '{}',
    rationale_json  TEXT,
    enrichment_json TEXT,
    next_action     TEXT,
    scored_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(org_id, run_date)
);

CREATE INDEX IF NOT EXISTS idx_prospect_scores_org  ON prospect_scores(org_id);
CREATE INDEX IF NOT EXISTS idx_prospect_scores_date ON prospect_scores(run_date);

UPDATE schema_version SET version = 20 WHERE version < 20;
