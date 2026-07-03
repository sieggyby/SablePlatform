-- 081_cost_operator_attribution.sql
-- Per-operator attribution on the cost ledger. cost_events has always been org-scoped
-- only, so measured per-HUMAN dollars were impossible -- the SableWeb cost report could
-- only project (generation counts x an estimate rate). This adds a nullable operator_id
-- stamped by callers that act on behalf of a logged-in operator (Slopper /reply,
-- /compose, deck produce, meme produce). The value is the stable SableWeb SESSION
-- identity (operator_arf / operator_ben / client_bharat), NOT the persona X-handle --
-- personas are shared across humans, so a persona handle can never answer "what did
-- ben spend" (see reply_suggestions.operator_handle for the persona namespace).
--
-- NULL means unattributed: every pre-081 row, plus system paths with no acting human
-- (weekly workflows, ambient deck generation, sweep timers). Reports must treat NULL
-- as "system / unattributed", never fold it into an operator.
--
-- 100% ADDITIVE (ADD COLUMN + CREATE INDEX -- no rebuild, no drop, no data loss).
-- Comment hygiene: NO semicolons inside double-dash comment lines (the runner splits on the char).

ALTER TABLE cost_events ADD COLUMN operator_id TEXT;

CREATE INDEX idx_cost_operator ON cost_events(operator_id);

UPDATE schema_version SET version = 81 WHERE version < 81;
