-- Migration 015: Entity decay scores for churn prediction alerting.
-- Stores per-entity decay scores received from Cult Grader diagnostic output.

CREATE TABLE IF NOT EXISTS entity_decay_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id          TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    decay_score     REAL NOT NULL,
    risk_tier       TEXT NOT NULL,
    scored_at       TEXT NOT NULL DEFAULT (datetime('now')),
    run_date        TEXT,
    factors_json    TEXT,
    FOREIGN KEY (org_id) REFERENCES orgs(org_id),
    UNIQUE (org_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_decay_scores_org ON entity_decay_scores(org_id);
CREATE INDEX IF NOT EXISTS idx_decay_scores_tier ON entity_decay_scores(org_id, risk_tier);

UPDATE schema_version SET version = 15 WHERE version < 15;
