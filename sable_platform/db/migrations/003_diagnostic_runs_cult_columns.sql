-- Migration 003: add cult_doctor columns to diagnostic_runs
ALTER TABLE diagnostic_runs ADD COLUMN cult_run_id TEXT;
ALTER TABLE diagnostic_runs ADD COLUMN project_slug TEXT;
ALTER TABLE diagnostic_runs ADD COLUMN run_date TEXT;
ALTER TABLE diagnostic_runs ADD COLUMN research_mode TEXT;
ALTER TABLE diagnostic_runs ADD COLUMN checkpoint_path TEXT;
ALTER TABLE diagnostic_runs ADD COLUMN overall_grade TEXT;
ALTER TABLE diagnostic_runs ADD COLUMN fit_score INTEGER;
ALTER TABLE diagnostic_runs ADD COLUMN recommended_action TEXT;
ALTER TABLE diagnostic_runs ADD COLUMN sable_verdict TEXT;
ALTER TABLE diagnostic_runs ADD COLUMN total_cost_usd REAL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_diagnostic_cult_run_id ON diagnostic_runs(cult_run_id);
CREATE INDEX IF NOT EXISTS idx_diagnostic_slug ON diagnostic_runs(project_slug);
UPDATE schema_version SET version = 3 WHERE version < 3;
