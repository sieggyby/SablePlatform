-- Migration 011: Alert cooldown support
ALTER TABLE alert_configs ADD COLUMN cooldown_hours INTEGER NOT NULL DEFAULT 4;
ALTER TABLE alerts ADD COLUMN last_delivered_at TEXT;
UPDATE schema_version SET version = 11 WHERE version < 11;
