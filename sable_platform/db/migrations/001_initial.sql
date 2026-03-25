BEGIN;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orgs (
    org_id       TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    discord_server_id TEXT,
    twitter_handle    TEXT,
    config_json  TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS entities (
    entity_id    TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES orgs(org_id),
    display_name TEXT,
    status       TEXT NOT NULL DEFAULT 'candidate',
    source       TEXT NOT NULL DEFAULT 'auto',
    config_json  TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_entities_org ON entities(org_id);

CREATE TABLE IF NOT EXISTS entity_handles (
    handle_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id  TEXT NOT NULL REFERENCES entities(entity_id),
    platform   TEXT NOT NULL,
    handle     TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    added_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(platform, handle)
);
CREATE INDEX IF NOT EXISTS idx_handles_entity          ON entity_handles(entity_id);
CREATE INDEX IF NOT EXISTS idx_handles_platform_handle ON entity_handles(platform, handle);

CREATE TABLE IF NOT EXISTS entity_tags (
    tag_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id      TEXT NOT NULL REFERENCES entities(entity_id),
    tag            TEXT NOT NULL,
    source         TEXT,
    confidence     REAL    NOT NULL DEFAULT 1.0,
    is_current     INTEGER NOT NULL DEFAULT 1,
    expires_at     TEXT,
    added_at       TEXT NOT NULL DEFAULT (datetime('now')),
    deactivated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tags_entity ON entity_tags(entity_id);
CREATE INDEX IF NOT EXISTS idx_tags_tag    ON entity_tags(tag);

CREATE TABLE IF NOT EXISTS entity_notes (
    note_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id  TEXT NOT NULL REFERENCES entities(entity_id),
    body       TEXT NOT NULL,
    source     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_notes_entity ON entity_notes(entity_id);

CREATE TABLE IF NOT EXISTS merge_candidates (
    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_a_id  TEXT NOT NULL REFERENCES entities(entity_id),
    entity_b_id  TEXT NOT NULL REFERENCES entities(entity_id),
    confidence   REAL NOT NULL DEFAULT 0.0,
    reason       TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(entity_a_id, entity_b_id)
);
CREATE INDEX IF NOT EXISTS idx_merge_candidates_status ON merge_candidates(status);

CREATE TABLE IF NOT EXISTS merge_events (
    event_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entity_id TEXT NOT NULL,
    target_entity_id TEXT NOT NULL,
    candidate_id     INTEGER REFERENCES merge_candidates(candidate_id),
    merged_by        TEXT,
    snapshot_json    TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS content_items (
    item_id       TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL REFERENCES orgs(org_id),
    entity_id     TEXT REFERENCES entities(entity_id),
    content_type  TEXT,
    platform      TEXT,
    external_id   TEXT,
    body          TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    posted_at     TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_content_org    ON content_items(org_id);
CREATE INDEX IF NOT EXISTS idx_content_entity ON content_items(entity_id);

CREATE TABLE IF NOT EXISTS diagnostic_runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id       TEXT NOT NULL REFERENCES orgs(org_id),
    run_type     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'running',
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    result_json  TEXT,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_diagnostic_org ON diagnostic_runs(org_id);

CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES orgs(org_id),
    job_type    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_jobs_org    ON jobs(org_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS job_steps (
    step_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL REFERENCES jobs(job_id),
    step_name    TEXT NOT NULL,
    step_order   INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'pending',
    retries      INTEGER NOT NULL DEFAULT 0,
    input_json   TEXT NOT NULL DEFAULT '{}',
    output_json  TEXT,
    error        TEXT,
    started_at   TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_steps_job ON job_steps(job_id);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id        TEXT NOT NULL REFERENCES orgs(org_id),
    job_id        TEXT REFERENCES jobs(job_id),
    artifact_type TEXT NOT NULL,
    path          TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    stale         INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_artifacts_org  ON artifacts(org_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(artifact_type);

CREATE TABLE IF NOT EXISTS cost_events (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id        TEXT NOT NULL REFERENCES orgs(org_id),
    job_id        TEXT REFERENCES jobs(job_id),
    call_type     TEXT NOT NULL,
    model         TEXT,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL    NOT NULL DEFAULT 0.0,
    call_status   TEXT NOT NULL DEFAULT 'success',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cost_org     ON cost_events(org_id);
CREATE INDEX IF NOT EXISTS idx_cost_created ON cost_events(created_at);

CREATE TABLE IF NOT EXISTS sync_runs (
    sync_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id          TEXT NOT NULL REFERENCES orgs(org_id),
    sync_type       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running',
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at    TEXT,
    records_synced  INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_org ON sync_runs(org_id);

INSERT INTO schema_version (version) VALUES (1);

COMMIT;
