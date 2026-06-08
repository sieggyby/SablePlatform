-- Add reply_outcomes.detected_via to distinguish AUTO-detected posted replies (the
-- scheduled persona-timeline scan, value 'auto') from operator-confirmed Mark-posted
-- rows (value 'operator'). 100 percent ADDITIVE: one ALTER TABLE ADD COLUMN, nullable,
-- no default, no rebuild. Legacy rows stay NULL. Comment hygiene: no semicolons in
-- -- comments (the runner splits on semicolons).
ALTER TABLE reply_outcomes ADD COLUMN detected_via TEXT;
UPDATE schema_version SET version = 69 WHERE version < 69;
