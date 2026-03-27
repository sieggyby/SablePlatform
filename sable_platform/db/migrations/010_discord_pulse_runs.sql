CREATE TABLE IF NOT EXISTS discord_pulse_runs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id                TEXT NOT NULL,
    project_slug          TEXT NOT NULL,
    run_date              TEXT NOT NULL,
    wow_retention_rate    REAL,
    echo_rate             REAL,
    avg_silence_gap_hours REAL,
    weekly_active_posters INTEGER,
    retention_delta       REAL,
    echo_rate_delta       REAL,
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (org_id, project_slug, run_date)
);

CREATE INDEX IF NOT EXISTS idx_discord_pulse_runs_org_date
    ON discord_pulse_runs (org_id, run_date);

UPDATE schema_version SET version = 10 WHERE version < 10;
