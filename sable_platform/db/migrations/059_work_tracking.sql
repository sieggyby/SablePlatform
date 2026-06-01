-- 059_work_tracking.sql
-- Operator work-tracking (SW-TASKING Phase 1): mod-slot "clock-in" sessions and
-- a generic operator work-event log, feeding the ops "scale of work delivered"
-- report. Replies are NOT mirrored here -- they are counted from reply_outcomes
-- (mig 056) so there is exactly one source of truth for replies.
-- See SableWeb/docs/SW_TASKING_PHASE1_PLAN.md.
--
-- Comment hygiene: no semicolons inside double-dash comment lines (the runner
-- in connection.py splits on the literal semicolon character).
-- Column conventions: TEXT primary keys (app-generated uuid hex), counts
-- INTEGER, all _at columns TEXT with a strftime ISO-8601-Z default (the
-- migration 053 timestamp contract). The note column is ops-only and must
-- never reach a client surface.

CREATE TABLE IF NOT EXISTS mod_slot_sessions (
    session_id         TEXT PRIMARY KEY,
    org_id             TEXT NOT NULL REFERENCES orgs(org_id),
    operator_handle    TEXT NOT NULL,
    started_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ended_at           TEXT,
    chats_watched_json TEXT NOT NULL DEFAULT '[]',
    note               TEXT,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS operator_work_events (
    event_id        TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES orgs(org_id),
    operator_handle TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    occurred_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ref_json        TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_mod_slot_sessions_org ON mod_slot_sessions(org_id, started_at);
CREATE INDEX IF NOT EXISTS ix_mod_slot_sessions_operator ON mod_slot_sessions(operator_handle, ended_at);
CREATE INDEX IF NOT EXISTS ix_operator_work_events_org ON operator_work_events(org_id, occurred_at);

UPDATE schema_version SET version = 59 WHERE version < 59;
