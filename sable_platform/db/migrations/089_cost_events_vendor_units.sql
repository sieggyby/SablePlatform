-- 089_cost_events_vendor_units.sql
-- Non-token vendor spend on the cost ledger. cost_events was built for LLM calls, so
-- its only raw quantities are input_tokens/output_tokens -- a credits-billed vendor
-- (Higgsfield first) had nowhere to keep its raw units, and a USD figure computed at
-- log time can never be recomputed when the plan's credit rate changes.
--
--   credits          -- raw vendor credits charged for the job (NULL on token rows)
--   credit_rate_usd  -- USD-per-credit rate used at log time (NULL on token rows)
--   note             -- free-text operator context, e.g. the vendor job id + what
--                       the job was for -- manual ledger entries need a "what was
--                       this" that call_type/model cannot carry
--
-- cost_usd stays the canonical spend figure (cost_usd = credits x credit_rate_usd
-- for vendor rows). NULL means token-based or pre-089 row.
--
-- 100% ADDITIVE (ADD COLUMN only -- no rebuild, no drop, no data loss).
-- Comment hygiene: NO semicolons inside double-dash comment lines (the runner splits on the char).

ALTER TABLE cost_events ADD COLUMN credits REAL;

ALTER TABLE cost_events ADD COLUMN credit_rate_usd REAL;

ALTER TABLE cost_events ADD COLUMN note TEXT;

UPDATE schema_version SET version = 89 WHERE version < 89;
