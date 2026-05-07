-- Migration 034: Grok-enrichment fields on kol_candidates.
--
-- Six new columns to capture qualitative + quantitative data Grok returns
-- when we ask it to look up a handle:
--
--   listed_count       INTEGER  Grok-reported count of X lists this account is on
--                                (separate from our internal multi-list signal).
--                                A 50K-listed account is meta-curated.
--   tweets_count       INTEGER  Total tweets/statuses
--   following_count    INTEGER  Accounts they follow
--   credibility_signal TEXT     One of: high | medium | low | unclear
--   real_name_known    INTEGER  NOT NULL DEFAULT 0 — set to 1 if Grok can identify
--                                a public real-name person behind the account
--   notes              TEXT     One-line context like "Cofounder of Ethereum"

ALTER TABLE kol_candidates ADD COLUMN listed_count INTEGER;
ALTER TABLE kol_candidates ADD COLUMN tweets_count INTEGER;
ALTER TABLE kol_candidates ADD COLUMN following_count INTEGER;
ALTER TABLE kol_candidates ADD COLUMN credibility_signal TEXT;
ALTER TABLE kol_candidates ADD COLUMN real_name_known INTEGER NOT NULL DEFAULT 0;
ALTER TABLE kol_candidates ADD COLUMN notes TEXT;

CREATE INDEX IF NOT EXISTS idx_kol_candidates_credibility
    ON kol_candidates(credibility_signal) WHERE credibility_signal IS NOT NULL;

UPDATE schema_version SET version = 34 WHERE version < 34;
