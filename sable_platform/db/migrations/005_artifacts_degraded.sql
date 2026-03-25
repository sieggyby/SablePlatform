ALTER TABLE artifacts ADD COLUMN degraded INTEGER NOT NULL DEFAULT 0;
UPDATE schema_version SET version = 5 WHERE version < 5;
