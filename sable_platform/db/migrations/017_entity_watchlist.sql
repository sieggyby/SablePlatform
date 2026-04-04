CREATE TABLE IF NOT EXISTS entity_watchlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id      TEXT NOT NULL REFERENCES orgs(org_id),
    entity_id   TEXT NOT NULL,
    added_by    TEXT NOT NULL,
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(org_id, entity_id)
);

CREATE TABLE IF NOT EXISTS watchlist_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id      TEXT NOT NULL REFERENCES orgs(org_id),
    entity_id   TEXT NOT NULL,
    decay_score REAL,
    tags_json   TEXT,
    interaction_count INTEGER,
    snapshot_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_watchlist_org ON entity_watchlist(org_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_snap ON watchlist_snapshots(org_id, entity_id, snapshot_at);

UPDATE schema_version SET version = 17 WHERE version < 17;
