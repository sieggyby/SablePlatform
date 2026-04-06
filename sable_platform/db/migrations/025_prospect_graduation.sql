-- Migration 025: Prospect graduation marker
ALTER TABLE prospect_scores ADD COLUMN graduated_at TEXT;
UPDATE schema_version SET version = 25 WHERE version < 25;
