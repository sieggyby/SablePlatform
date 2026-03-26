-- Feature 3: Member Journey Tracking
CREATE TABLE IF NOT EXISTS entity_tag_history (
    history_id   TEXT PRIMARY KEY,
    entity_id    TEXT NOT NULL REFERENCES entities(entity_id),
    org_id       TEXT NOT NULL,
    change_type  TEXT NOT NULL,
    tag          TEXT NOT NULL,
    confidence   REAL,
    source       TEXT,
    source_ref   TEXT,
    expires_at   TEXT,
    effective_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tag_history_entity ON entity_tag_history(entity_id, effective_at);
CREATE INDEX IF NOT EXISTS idx_tag_history_org    ON entity_tag_history(org_id, tag, effective_at);

UPDATE schema_version SET version = 8 WHERE version < 8;
