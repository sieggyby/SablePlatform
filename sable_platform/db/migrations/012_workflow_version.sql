-- Migration 012: Workflow config versioning (step fingerprint)
ALTER TABLE workflow_runs ADD COLUMN step_fingerprint TEXT;
UPDATE schema_version SET version = 12 WHERE version < 12;
