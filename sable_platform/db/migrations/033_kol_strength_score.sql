-- Migration 033: KOL strength scoring + paid-enrichment fields on kol_candidates.
--
-- Adds three columns:
--   kol_strength_score   REAL    project-independent KOL strength (0-1).
--                                Computed by `sable-kol enrich --score`.
--                                Wired into match.py as a 6th signal.
--   verified             INTEGER NOT NULL DEFAULT 0
--                                Twitter "verified" flag, populated by SocialData enrich.
--   account_created_at   TEXT    Twitter account creation timestamp, populated by enrich.
--
-- These power the new `sable-kol enrich` flow and the matcher's kol_strength signal.

ALTER TABLE kol_candidates ADD COLUMN kol_strength_score REAL;
ALTER TABLE kol_candidates ADD COLUMN verified INTEGER NOT NULL DEFAULT 0;
ALTER TABLE kol_candidates ADD COLUMN account_created_at TEXT;

CREATE INDEX IF NOT EXISTS idx_kol_candidates_strength
    ON kol_candidates(kol_strength_score) WHERE kol_strength_score IS NOT NULL;

UPDATE schema_version SET version = 33 WHERE version < 33;
