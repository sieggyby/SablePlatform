-- Migration 006: add workflow orchestration tables
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id           TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL REFERENCES orgs(org_id),
    workflow_name    TEXT NOT NULL,
    workflow_version TEXT NOT NULL DEFAULT '1.0',
    status           TEXT NOT NULL DEFAULT 'pending',
    config_json      TEXT,
    started_at       TEXT,
    completed_at     TEXT,
    error            TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_org  ON workflow_runs(org_id);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_name ON workflow_runs(workflow_name, status);

CREATE TABLE IF NOT EXISTS workflow_steps (
    step_id      TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES workflow_runs(run_id),
    step_name    TEXT NOT NULL,
    step_index   INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    retries      INTEGER NOT NULL DEFAULT 0,
    input_json   TEXT,
    output_json  TEXT,
    error        TEXT,
    started_at   TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_workflow_steps_run ON workflow_steps(run_id);

CREATE TABLE IF NOT EXISTS workflow_events (
    event_id     TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES workflow_runs(run_id),
    step_id      TEXT,
    event_type   TEXT NOT NULL,
    payload_json TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_workflow_events_run ON workflow_events(run_id);

UPDATE schema_version SET version = 6 WHERE version < 6;
