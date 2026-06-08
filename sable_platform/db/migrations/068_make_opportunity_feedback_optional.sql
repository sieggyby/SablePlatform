-- Make relay_opportunity_feedback.opportunity_id NULLABLE (freeform-draft variant thumbs
-- have a suggestion_id but no opportunity). Leaf table (nothing references it), so a
-- rebuild is safe under foreign_keys=ON. Comment hygiene: no semicolons in -- comments.
CREATE TABLE relay_opportunity_feedback_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id  INTEGER REFERENCES relay_reply_opportunities(id),
    suggestion_id   TEXT REFERENCES reply_suggestions(id),
    rater_handle    TEXT NOT NULL,
    rater_role      TEXT NOT NULL,
    thumb           INTEGER NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
INSERT INTO relay_opportunity_feedback_new SELECT id, opportunity_id, suggestion_id, rater_handle, rater_role, thumb, created_at FROM relay_opportunity_feedback;
DROP TABLE relay_opportunity_feedback;
ALTER TABLE relay_opportunity_feedback_new RENAME TO relay_opportunity_feedback;
CREATE INDEX IF NOT EXISTS ix_relay_opportunity_feedback_opp ON relay_opportunity_feedback(opportunity_id);
UPDATE schema_version SET version = 68 WHERE version < 68;
