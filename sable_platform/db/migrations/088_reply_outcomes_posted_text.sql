-- Add reply_outcomes.posted_text: the reply text the operator ACTUALLY posted, captured
-- at link time by the detect/reconcile jobs that already hold it in memory (zero extra
-- SocialData spend). was_edited says THAT the operator changed our draft, this says WHAT
-- they changed it to -- the grounding for the edit-diff learning loop (style proposals
-- learn from operator corrections, not just thumbs-down). 100 percent ADDITIVE: one
-- ALTER TABLE ADD COLUMN, nullable, no default, no rebuild. Legacy rows stay NULL.
-- Comment hygiene: no semicolons in -- comments (the runner splits on semicolons).
ALTER TABLE reply_outcomes ADD COLUMN posted_text TEXT;
UPDATE schema_version SET version = 88 WHERE version < 88;
