-- Migration 035: location column on kol_candidates.
--
-- Stores the X profile location string as Grok returns it (e.g. "NYC",
-- "Singapore", "London / SF"). Free-form text — no normalization at write
-- time. Phase 2 work could canonicalize to country/region/timezone for
-- regional matching like "KOLs based in Asia for an APAC launch".

ALTER TABLE kol_candidates ADD COLUMN location TEXT;

UPDATE schema_version SET version = 35 WHERE version < 35;
