ALTER TABLE prospect_scores ADD COLUMN rejected_at TEXT;

UPDATE schema_version SET version = 26 WHERE version < 26;
