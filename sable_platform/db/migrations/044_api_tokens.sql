-- Migration 044: api_tokens for the alert-triage HTTP API MVP.
-- See TODO_API.md / docs/API_ALERT_TRIAGE_MVP.md.
--
-- token_id      Short user-visible identifier (also the wire prefix).
-- token_hash    SHA-256 hex of the full secret. Raw secret is NEVER stored.
-- scopes_json   JSON array of strings from: read_only, write_safe,
--               spend_request, spend_execute.
-- org_scopes_json  JSON array of org_id strings. ["*"] grants all orgs
--                  (owner-only convention, not currently mintable via CLI).
-- expires_at    Optional ISO timestamp. NULL = no expiry.
-- enabled       0 disables token immediately. Soft revoke retains the row.

CREATE TABLE IF NOT EXISTS api_tokens (
    token_id        TEXT PRIMARY KEY,
    token_hash      TEXT NOT NULL,
    label           TEXT NOT NULL,
    operator_id     TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT,
    last_used_at    TEXT,
    revoked_at      TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1,
    scopes_json     TEXT NOT NULL DEFAULT '["read_only"]',
    org_scopes_json TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_api_tokens_enabled
    ON api_tokens(enabled, expires_at);
CREATE INDEX IF NOT EXISTS idx_api_tokens_operator
    ON api_tokens(operator_id);

UPDATE schema_version SET version = 44 WHERE version < 44;
