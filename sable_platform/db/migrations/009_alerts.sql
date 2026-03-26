-- Feature 4: Proactive Alerting
CREATE TABLE IF NOT EXISTS alert_configs (
    config_id           TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL REFERENCES orgs(org_id),
    min_severity        TEXT NOT NULL DEFAULT 'warning',
    telegram_chat_id    TEXT,
    discord_webhook_url TEXT,
    enabled             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(org_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id         TEXT PRIMARY KEY,
    org_id           TEXT REFERENCES orgs(org_id),
    alert_type       TEXT NOT NULL,
    severity         TEXT NOT NULL,
    title            TEXT NOT NULL,
    body             TEXT,
    entity_id        TEXT REFERENCES entities(entity_id),
    action_id        TEXT REFERENCES actions(action_id),
    run_id           TEXT REFERENCES workflow_runs(run_id),
    data_json        TEXT,
    status           TEXT NOT NULL DEFAULT 'new',
    dedup_key        TEXT,
    acknowledged_at  TEXT,
    acknowledged_by  TEXT,
    resolved_at      TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_alerts_org    ON alerts(org_id, status, severity);
CREATE INDEX IF NOT EXISTS idx_alerts_dedup  ON alerts(dedup_key) WHERE dedup_key IS NOT NULL AND status = 'new';
CREATE INDEX IF NOT EXISTS idx_alert_configs ON alert_configs(org_id);

UPDATE schema_version SET version = 9 WHERE version < 9;
