-- Migration 024: Operator identity on workflow_runs + compound index on entity_tags
ALTER TABLE workflow_runs ADD COLUMN operator_id TEXT;
CREATE INDEX IF NOT EXISTS idx_entity_tags_tag_current ON entity_tags(tag, is_current);
UPDATE schema_version SET version = 24 WHERE version < 24;
