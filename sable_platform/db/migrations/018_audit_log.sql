CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    org_id      TEXT,
    entity_id   TEXT,
    detail_json TEXT,
    source      TEXT NOT NULL DEFAULT 'cli'
);

CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_log(org_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor, timestamp);

UPDATE schema_version SET version = 18 WHERE version < 18;
