-- Migration 004: add completion tracking to jobs
ALTER TABLE jobs ADD COLUMN completed_at TEXT;
ALTER TABLE jobs ADD COLUMN result_json TEXT;
ALTER TABLE jobs ADD COLUMN error_message TEXT;
UPDATE schema_version SET version = 4 WHERE version < 4;
