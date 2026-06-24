-- 078_operator_meme_budget.sql
-- Per-(operator, org, ISO-week) DOLLAR budget for ON-DEMAND meme production
-- (the SableWeb deck "Generate" button -> Slopper POST /api/v1/meme/produce).
-- An accumulator row per operator/client/week: each produce RESERVES an estimate up
-- front (refunded if it would breach the cap), then RECONCILES to the ideate call's
-- ACTUAL cost. Cap defaults to 5.00 per operator per client per week, overridable per
-- org via orgs.config_json.max_meme_usd_per_operator_per_week.
-- The produced candidates land in the SHARED org deck (content_candidates) -- so the
-- BUDGET is per-operator while the OUTPUT bank is shared across the client's operators.
-- See Sable_Slopper/docs/MEME_ENGINE_PLAN.md.
--
-- Comment hygiene: no semicolons inside double-dash comment lines (the connection.py
-- runner splits on the literal semicolon character). Money is REAL, counts INTEGER,
-- _at columns are TEXT with a strftime default. No FK on org_id (mirrors
-- operator_reply_quota) -- the org is validated at the serve layer.

CREATE TABLE IF NOT EXISTS operator_meme_budget (
    operator_handle TEXT NOT NULL,
    org_id          TEXT NOT NULL,
    week_iso        TEXT NOT NULL,
    spend_usd       REAL NOT NULL DEFAULT 0,
    runs            INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (operator_handle, org_id, week_iso)
);
