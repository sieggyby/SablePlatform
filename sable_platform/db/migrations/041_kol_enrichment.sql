-- Migration 041: per-candidate Grok enrichment cache (KO-3 redesign).
--
-- The KOL wizard's KO-3 v1 shipped as a "draft cold-intro" that wrote a
-- 2-3 line opener directly. Operator feedback (2026-05-10): the drafts
-- were unusable as DMs, and that's not actually what's needed. The
-- redesign: Grok returns INTEL the operator uses to write their own
-- outreach -- likes/dislikes/location/recent-themes/communities/mutuals/
-- top-tweets/commentary -- plus an explicit commonality_with_operator
-- block computed from the operator's persona profile in-prompt.
--
-- This table caches the enrichment payload per (candidate, operator) so
-- the same intel can be re-rendered without re-billing xAI. Refresh is
-- operator-driven (per the build plan: soft TTL, render cached + badge
-- staleness, manual refresh button). Each refresh writes a NEW row;
-- history is preserved so operator can see what's changed about a
-- target between fetches.
--
-- Lookup pattern (SableWeb route):
--     SELECT payload_json, fetched_at, grok_model, cost_usd
--       FROM kol_enrichment
--      WHERE candidate_id = $1 AND operator_email = $2
--   ORDER BY fetched_at DESC
--      LIMIT 1
--
-- IMPORTANT: never embed a literal semicolon inside a -- comment. The runner
-- splits on raw semicolons, so a comment-semicolon creates a phantom SQL
-- statement that breaks init.

CREATE TABLE IF NOT EXISTS kol_enrichment (
    enrichment_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id      INTEGER NOT NULL REFERENCES kol_candidates(candidate_id),
    -- Operator identity. We index on email (the canonical identity column on
    -- kol_create_audit) so the SableWeb route's lookup matches its session
    -- without a join. operator_persona is a denormalized convenience -- the
    -- email-to-persona map could change in the future, and we want to know
    -- which voice profile generated a given cached payload.
    operator_email    TEXT NOT NULL,
    operator_persona  TEXT NOT NULL,
    fetched_at        TEXT NOT NULL DEFAULT (datetime('now')),
    -- Full Grok response payload, including the structured fields
    -- (likes/dislikes/location/etc.) plus the prose blocks
    -- (commonality_with_operator/commentary). Schema lives in
    -- sable_kol/preflight_schemas.py::Enrichment -- versioned via the
    -- payload's own signal_metadata + a payload_schema_version field
    -- inside the JSON. We do NOT split into columns because the schema
    -- will iterate fast (operators tune which fields are useful).
    payload_json      TEXT NOT NULL,
    grok_model        TEXT,
    cost_usd          REAL DEFAULT 0
);

-- Composite index on the lookup pattern. Postgres-friendly: DESC sort is
-- captured in the index, so the LIMIT 1 stays an O(log n) tree walk.
CREATE INDEX IF NOT EXISTS idx_kol_enrichment_lookup
    ON kol_enrichment(candidate_id, operator_email, fetched_at DESC);

-- Independent index on operator_email so per-operator analytics ("how many
-- candidates has @arf enriched this month?") don't full-scan.
CREATE INDEX IF NOT EXISTS idx_kol_enrichment_operator
    ON kol_enrichment(operator_email);

UPDATE schema_version SET version = 41 WHERE version < 41;
