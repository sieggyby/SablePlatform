-- Migration 013: Track last delivery failure on alert records
ALTER TABLE alerts ADD COLUMN last_delivery_error TEXT;
UPDATE schema_version SET version = 13 WHERE version < 13;
