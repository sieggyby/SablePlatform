-- Migration 042: per-row review state on kol_create_audit.
--
-- The /ops/kol-network picker shows a "+ New project" button to every
-- operator on KOL_CREATE_EMAILS. Operator-laid spec (sieggy 2026-05-10):
-- each user can generate ONE map at a time, then is blocked until an admin
-- inspects the result and clears the hold. The existing audit row already
-- captures who submitted what -- we just add a status column the picker
-- and the admin page can both read and write.
--
-- Three columns:
--   review_status  TEXT NOT NULL DEFAULT 'approved'
--                  ('pending'|'approved'|'rejected'). Default is 'approved'
--                  so historical wizard submissions inserted before this
--                  migration are NOT silently treated as pending -- otherwise
--                  every operator on the allowlist would see their map-create
--                  button greyed out the moment this migration ran. The web
--                  wizard's recordAudit() write path stamps 'pending'
--                  explicitly for new submissions, so only those count.
--   reviewed_by    TEXT NULL  -- admin email at decision
--   reviewed_at    TEXT NULL  -- ISO timestamp at decision
--
-- The picker counts rows with email=$1 AND review_status='pending' AND
-- endpoint='/api/ops/kol-network/create'. The admin page mutates
-- review_status + stamps reviewed_by/reviewed_at when an admin acts.
--
-- IMPORTANT: never embed a literal semicolon inside a -- comment. The runner
-- splits on raw semicolons, so a comment-semicolon creates a phantom SQL
-- statement that breaks init.

ALTER TABLE kol_create_audit ADD COLUMN review_status TEXT NOT NULL DEFAULT 'approved';
ALTER TABLE kol_create_audit ADD COLUMN reviewed_by   TEXT;
ALTER TABLE kol_create_audit ADD COLUMN reviewed_at   TEXT;

-- Composite index supporting the picker's per-user pending count
-- (WHERE email=$1 AND review_status='pending' AND endpoint=$2).
CREATE INDEX IF NOT EXISTS idx_kol_create_audit_review
    ON kol_create_audit(email, review_status, endpoint);

UPDATE schema_version SET version = 42 WHERE version < 42;
