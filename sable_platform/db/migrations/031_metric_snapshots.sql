-- Migration 031: metric_snapshots — week-over-week persistence for client check-ins.
--
-- Each Friday the client_checkin_loop workflow snapshots an org's tier-1 + tier-2
-- metrics so the following week's run can compute honest WoW deltas.
-- One row per (org_id, snapshot_date). source distinguishes inputs:
--   'cult_grader'  — derived from the latest computed_metrics.json
--   'pipeline'     — synthesized by the check-in workflow itself
--   'manual'       — operator-entered (e.g. trial baseline backfill)
CREATE TABLE IF NOT EXISTS metric_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id        TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    metrics_json  TEXT NOT NULL DEFAULT '{}',
    source        TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(org_id, snapshot_date),
    FOREIGN KEY (org_id) REFERENCES orgs(org_id)
);

CREATE INDEX IF NOT EXISTS idx_metric_snapshots_org_date
    ON metric_snapshots(org_id, snapshot_date);

UPDATE schema_version SET version = 31 WHERE version < 31;
