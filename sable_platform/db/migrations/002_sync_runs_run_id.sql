-- Migration 002: add cult_run_id and count columns to sync_runs
ALTER TABLE sync_runs ADD COLUMN cult_run_id TEXT;
ALTER TABLE sync_runs ADD COLUMN entities_created INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sync_runs ADD COLUMN entities_updated INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sync_runs ADD COLUMN handles_added INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sync_runs ADD COLUMN tags_added INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sync_runs ADD COLUMN tags_replaced INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sync_runs ADD COLUMN merge_candidates_created INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_sync_cult_run_id ON sync_runs(cult_run_id);
UPDATE schema_version SET version = 2 WHERE version < 2;
