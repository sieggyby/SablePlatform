-- Migration 022: playbook outcome tagging tables (F-PBTAG)
CREATE TABLE IF NOT EXISTS playbook_targets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id          TEXT NOT NULL REFERENCES orgs(org_id),
    artifact_id     TEXT,
    targets_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_playbook_targets_org ON playbook_targets(org_id);

CREATE TABLE IF NOT EXISTS playbook_outcomes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id              TEXT NOT NULL REFERENCES orgs(org_id),
    targets_artifact_id TEXT,
    outcomes_json       TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_playbook_outcomes_org ON playbook_outcomes(org_id);

UPDATE schema_version SET version = 22 WHERE version < 22;
