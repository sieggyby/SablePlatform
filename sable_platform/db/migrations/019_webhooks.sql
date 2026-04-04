CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id          TEXT NOT NULL REFERENCES orgs(org_id),
    url             TEXT NOT NULL,
    event_types     TEXT NOT NULL,
    secret          TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_failure_at TEXT,
    last_failure_error TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(org_id, url)
);

UPDATE schema_version SET version = 19 WHERE version < 19;
