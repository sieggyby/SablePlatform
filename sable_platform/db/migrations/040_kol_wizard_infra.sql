-- Migration 040: KOL wizard infrastructure.
--
-- Three orthogonal additions, all required by the any-project KOL wizard
-- (~/Projects/SableKOL/docs/any_project_wizard_plan.md, v3 Codex round 2):
--
--   1. kol_create_audit         New append-only auth audit log for the
--                                /api/ops/kol-network/* endpoints.
--                                email is NULLABLE so anonymous /
--                                unauthenticated failures (outcome=auth_failed)
--                                can still log without violating NOT NULL.
--   2. jobs.worker_id           New column. Generic infrastructure used by the
--                                claim_next_job() helper added in Phase C of
--                                the wizard build -- not KOL-specific, benefits
--                                all future job_types.
--   3. job_steps.next_retry_at  New column. Set when a step is deferred via
--                                429 backoff -- worker only attempts steps where
--                                next_retry_at IS NULL OR next_retry_at <= now.
--
-- The runner in connection.py wraps each migration's statements in a single
-- "with conn:" block, so we do NOT include explicit BEGIN/COMMIT here (a
-- nested BEGIN would raise "cannot start a transaction within a transaction").
--
-- IMPORTANT: never embed a literal semicolon inside a -- comment. The runner
-- splits on raw semicolons, so a comment-semicolon creates a phantom SQL
-- statement that breaks init.

-- (1) Audit table. PII-bearing (submitter email) -- 90-day retention via cron.
CREATE TABLE IF NOT EXISTS kol_create_audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at_utc       TEXT NOT NULL DEFAULT (datetime('now')),
    email        TEXT,
    endpoint     TEXT NOT NULL,
    method       TEXT NOT NULL,
    outcome      TEXT NOT NULL,
    job_id       TEXT REFERENCES jobs(job_id),
    ip           TEXT,
    user_agent   TEXT
);

CREATE INDEX IF NOT EXISTS idx_kol_create_audit_email
    ON kol_create_audit(email);
CREATE INDEX IF NOT EXISTS idx_kol_create_audit_at
    ON kol_create_audit(at_utc);
CREATE INDEX IF NOT EXISTS idx_kol_create_audit_outcome
    ON kol_create_audit(outcome);

-- (2) Generic worker_id on jobs -- not KOL-specific, benefits all job_types.
ALTER TABLE jobs ADD COLUMN worker_id TEXT;
CREATE INDEX IF NOT EXISTS idx_jobs_worker ON jobs(worker_id);

-- (3) Deferred-retry timestamp on job_steps.
ALTER TABLE job_steps ADD COLUMN next_retry_at TEXT;
CREATE INDEX IF NOT EXISTS idx_job_steps_next_retry ON job_steps(next_retry_at);

UPDATE schema_version SET version = 40 WHERE version < 40;
