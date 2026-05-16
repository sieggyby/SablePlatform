-- Migration 048: Airlock — invite-source-aware member verification (sable-roles).
-- Adds 3 tables for the airlock feature per
-- ~/Projects/SolStitch/internal/airlock_plan.md sec 2.
--
-- 2.1 discord_invite_snapshot  — bot cache of guild.invites() state, diffed per join
-- 2.2 discord_team_inviters    — allowlist of Sable-team user-IDs whose invites bypass
-- 2.3 discord_member_admit     — per-join ledger w/ airlock state machine
--
-- Comment-hygiene reminder: no semicolons inside double-dash comment lines.
-- The runner in connection.py splits on the literal semicolon character.

-- 2.1 Invite snapshot — bot's local cache of guild.invites() state
-- (code, uses, inviter, max_uses, expires_at). The on_member_join handler
-- diffs guild.invites() against this snapshot to attribute the join to
-- the invite whose uses incremented. UNIQUE(guild_id, code) so the
-- snapshot is keyed and re-fetches UPSERT cleanly.
CREATE TABLE IF NOT EXISTS discord_invite_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        TEXT NOT NULL,
    code            TEXT NOT NULL,
    inviter_user_id TEXT,
    uses            INTEGER NOT NULL DEFAULT 0,
    max_uses        INTEGER NOT NULL DEFAULT 0,
    expires_at      TEXT,
    captured_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(guild_id, code)
);

CREATE INDEX IF NOT EXISTS idx_discord_invite_snapshot_guild
    ON discord_invite_snapshot (guild_id);

-- 2.2 Team-inviter allowlist — whose invites bypass airlock.
-- Bootstrap via SABLE_ROLES_TEAM_INVITERS_JSON env on bot boot (UPSERT,
-- idempotent). Runtime mgmt via /add-team-inviter slash command.
-- Past invites grandfather — removing a user from the allowlist does NOT
-- retroactively invalidate invites they already created.
CREATE TABLE IF NOT EXISTS discord_team_inviters (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id  TEXT NOT NULL,
    user_id   TEXT NOT NULL,
    added_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    added_by  TEXT NOT NULL,
    UNIQUE(guild_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_discord_team_inviters_guild
    ON discord_team_inviters (guild_id);

-- 2.3 Per-join admit ledger — every on_member_join writes a row here.
-- airlock_status state machine:
--   held              — non-team invite, awaiting mod triage
--   auto_admitted     — team-invite attribution, immediately granted member role
--   admitted          — mod ran /admit
--   banned            — mod ran /ban
--   kicked            — mod ran /kick (rejoinable — new join creates new row via REPLACE on UNIQUE)
--   left_during_airlock — user left voluntarily while held
-- UNIQUE(guild_id, user_id) so a rejoin overwrites the prior row via the
-- attempted-INSERT path (callers use ON CONFLICT DO UPDATE to refresh state).
CREATE TABLE IF NOT EXISTS discord_member_admit (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id                    TEXT NOT NULL,
    user_id                     TEXT NOT NULL,
    joined_at                   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    attributed_invite_code      TEXT,
    attributed_inviter_user_id  TEXT,
    is_team_invite              INTEGER NOT NULL DEFAULT 0,
    airlock_status              TEXT NOT NULL CHECK (
        airlock_status IN ('held', 'auto_admitted', 'admitted', 'banned', 'kicked', 'left_during_airlock')
    ),
    decision_by                 TEXT,
    decision_at                 TEXT,
    decision_reason             TEXT,
    UNIQUE(guild_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_discord_member_admit_status
    ON discord_member_admit (guild_id, airlock_status);

UPDATE schema_version SET version = 48 WHERE version < 48;
