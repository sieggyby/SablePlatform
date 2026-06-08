-- 075_allowlist_entries.sql
-- DB-backed SableWeb allowlist (ONBOARDING_PHASE2_PLAN.md P1). Lets operators add/remove
-- portal access from the CLI without a redeploy. This is an AUTH table -- OPS-ONLY, never
-- on /client. SableWeb merges these rows UNDER env/file (env/file always win), additive
-- only -- a row here can never escalate above or lock out an env/file user. `email` is the
-- lowercased PK (the CHECK enforces it so a mixed-case write can't silently miss the
-- lowercased lookup). `enabled=0` soft-disables (stops NEW logins within the cache TTL --
-- it does NOT revoke a live JWT before its 8h expiry).
-- Comment hygiene: NO semicolons inside double-dash comment lines (the runner splits on the
-- literal semicolon). All _at columns TEXT with the strftime ISO-8601-Z default.

CREATE TABLE allowlist_entries (
  email         TEXT PRIMARY KEY,
  role          TEXT NOT NULL,
  operator_id   TEXT,
  org           TEXT,
  assigned_orgs TEXT,
  enabled       INTEGER NOT NULL DEFAULT 1,
  notes         TEXT,
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  CHECK (role IN ('admin', 'operator', 'client', 'client_ops')),
  CHECK (email = lower(email))
);

UPDATE schema_version SET version = 75 WHERE version < 75;
