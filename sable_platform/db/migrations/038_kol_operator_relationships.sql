-- Migration 038: SableKOL operator-tagged relationships.
--
-- Append-only history table. Each row is one status change (or note write)
-- by one operator on one (client, KOL handle) pair. The "current state" of
-- a relationship is the row with MAX(created_at) for that (handle, client)
-- tuple.
--
-- Status enum:
--   dm_sent          — operator sent a DM, awaiting reply
--   replied          — they replied, neutral signal
--   replied_engaged  — they replied with interest
--   meeting          — call/meeting scheduled or completed
--   relationship     — operator personally knows them (warm contact)
--   pass             — operator decided not to pursue
--   blocked          — they blocked us OR we shouldn't approach
--
-- Visibility model (resolved in /grill-me Q7):
--   * is_private=0 (default)  — visible to all operators on this client_id
--   * is_private=1            — visible only to the writing operator_id
--   * cross-client visibility  — never. Tags are scoped to one client
--
-- See ~/Projects/SableKOL/docs/sableweb_kol_build_plan.md for the full
-- design context.

CREATE TABLE IF NOT EXISTS kol_operator_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handle_normalized TEXT NOT NULL,
    client_id TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    status TEXT NOT NULL,
    note TEXT,
    is_private INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (status IN ('dm_sent', 'replied', 'replied_engaged',
                      'meeting', 'relationship', 'pass', 'blocked')),
    CHECK (is_private IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_kor_handle_client
    ON kol_operator_relationships(handle_normalized, client_id);

CREATE INDEX IF NOT EXISTS idx_kor_operator
    ON kol_operator_relationships(operator_id, client_id);

CREATE INDEX IF NOT EXISTS idx_kor_created
    ON kol_operator_relationships(created_at);

UPDATE schema_version SET version = 38 WHERE version < 38;
