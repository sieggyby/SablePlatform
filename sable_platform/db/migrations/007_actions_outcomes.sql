-- Feature 1: Operator Action Layer
CREATE TABLE IF NOT EXISTS actions (
    action_id       TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES orgs(org_id),
    entity_id       TEXT REFERENCES entities(entity_id),
    content_item_id TEXT REFERENCES content_items(item_id),
    source          TEXT NOT NULL DEFAULT 'manual',
    source_ref      TEXT,
    action_type     TEXT NOT NULL DEFAULT 'general',
    title           TEXT NOT NULL,
    description     TEXT,
    operator        TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    claimed_at      TEXT,
    completed_at    TEXT,
    skipped_at      TEXT,
    outcome_notes   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_actions_org     ON actions(org_id, status);
CREATE INDEX IF NOT EXISTS idx_actions_entity  ON actions(entity_id) WHERE entity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_actions_pending ON actions(org_id, created_at) WHERE status = 'pending';

-- Feature 2: Outcome Tracking
CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id    TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL REFERENCES orgs(org_id),
    entity_id     TEXT REFERENCES entities(entity_id),
    action_id     TEXT REFERENCES actions(action_id),
    outcome_type  TEXT NOT NULL,
    description   TEXT,
    metric_name   TEXT,
    metric_before REAL,
    metric_after  REAL,
    metric_delta  REAL,
    data_json     TEXT,
    recorded_by   TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_outcomes_org    ON outcomes(org_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_action ON outcomes(action_id) WHERE action_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_outcomes_entity ON outcomes(entity_id) WHERE entity_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS diagnostic_deltas (
    delta_id       TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL REFERENCES orgs(org_id),
    run_id_before  INTEGER NOT NULL,
    run_id_after   INTEGER NOT NULL,
    metric_name    TEXT NOT NULL,
    value_before   REAL,
    value_after    REAL,
    delta          REAL,
    pct_change     REAL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_deltas_org   ON diagnostic_deltas(org_id, metric_name);
CREATE INDEX IF NOT EXISTS idx_deltas_after ON diagnostic_deltas(run_id_after);

UPDATE schema_version SET version = 7 WHERE version < 7;
