CREATE TABLE IF NOT EXISTS entity_centrality_scores (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id                  TEXT NOT NULL REFERENCES orgs(org_id),
    entity_id               TEXT NOT NULL,
    degree_centrality       REAL NOT NULL DEFAULT 0.0,
    betweenness_centrality  REAL NOT NULL DEFAULT 0.0,
    eigenvector_centrality  REAL NOT NULL DEFAULT 0.0,
    scored_at               TEXT NOT NULL DEFAULT (datetime('now')),
    run_date                TEXT NOT NULL,
    UNIQUE(org_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_centrality_org ON entity_centrality_scores(org_id);

UPDATE schema_version SET version = 16 WHERE version < 16;
