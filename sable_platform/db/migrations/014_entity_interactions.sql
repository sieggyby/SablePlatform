-- Migration 014: Entity interaction edges for relationship web visualization.
-- Stores directional interaction edges between entity handles (reply, mention, co_mention).

CREATE TABLE IF NOT EXISTS entity_interactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id          TEXT NOT NULL,
    source_handle   TEXT NOT NULL,
    target_handle   TEXT NOT NULL,
    interaction_type TEXT NOT NULL,
    count           INTEGER NOT NULL DEFAULT 1,
    first_seen      TEXT,
    last_seen       TEXT,
    run_date        TEXT,
    FOREIGN KEY (org_id) REFERENCES orgs(org_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_interactions_org ON entity_interactions(org_id);
CREATE INDEX IF NOT EXISTS idx_entity_interactions_source ON entity_interactions(org_id, source_handle);
CREATE INDEX IF NOT EXISTS idx_entity_interactions_type ON entity_interactions(org_id, interaction_type);

UPDATE schema_version SET version = 14 WHERE version < 14;
