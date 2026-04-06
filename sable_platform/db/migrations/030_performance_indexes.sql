-- Migration 030: Add performance indexes for alert evaluation and cost queries.

CREATE INDEX IF NOT EXISTS idx_entity_tags_current
    ON entity_tags(entity_id, is_current, tag);

CREATE INDEX IF NOT EXISTS idx_cost_events_org_date
    ON cost_events(org_id, created_at);

UPDATE schema_version SET version = 30 WHERE version < 30;
