-- Migration 021: add run_summary_json blob to diagnostic_runs for SableWeb
ALTER TABLE diagnostic_runs ADD COLUMN run_summary_json TEXT;

UPDATE schema_version SET version = 21 WHERE version < 21;
