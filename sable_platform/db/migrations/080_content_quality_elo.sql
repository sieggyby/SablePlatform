-- 080_content_quality_elo.sql
-- Content-preference Elo rollup for the Content Deck A/B duel (C3), parallel to mig 066's
-- media_rec_events -> media_quality. The deck's pairwise duel writes a row to content_deck_decisions
-- (winner=candidate_id, loser=pair_loser_id, decision='keep') with NO status flip -- a pure per-
-- operator preference signal. This migration makes those rows FOLDABLE forward-only and adds the Elo
-- rollup they fold into.
--
-- DUAL GRAIN. A content_candidate is ephemeral (a handful of votes, then soft-expire/GC), so a per-
-- candidate Elo never converges and dies with the candidate -- it is kept only as a live, within-deck
-- tie-break overlay (subject_kind='candidate', behind a caveat, never a verdict). The DURABLE signal
-- that feeds the Tweet-Quality engine is aggregated at FEATURE grain (subject_kind='feature',
-- subject_key='kind:<kind>' / 'template:<id>' / 'format:<fmt>' pulled from the candidate payload), which
-- accumulates across candidates and survives GC.
--
-- 100% ADDITIVE (ADD COLUMN + CREATE TABLE + CREATE INDEX -- no rebuild, no drop, no data loss), the
-- same property as mig 066. Existing content_deck_decisions rows default applied=0 and fold on the
-- first applier run (bounded -- the deck is new). NO cost column, ever.
-- Comment hygiene: NO semicolons inside double-dash comment lines (the runner splits on the char).

-- (1) make the swipe/duel log foldable forward-only (parallel to media_rec_events.applied)
ALTER TABLE content_deck_decisions ADD COLUMN applied INTEGER NOT NULL DEFAULT 0;

-- (2) the content-quality Elo rollup (parallel to media_quality), dual-grain
CREATE TABLE content_quality (
  org_id       TEXT NOT NULL,
  subject_kind TEXT NOT NULL,
  subject_key  TEXT NOT NULL,
  elo          REAL NOT NULL DEFAULT 1500,
  n_offered    INTEGER NOT NULL DEFAULT 0,
  n_chosen     INTEGER NOT NULL DEFAULT 0,
  updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (org_id, subject_kind, subject_key),
  CHECK (subject_kind IN ('candidate', 'feature'))
);

-- (3) unapplied-fold index (parallel to ix_media_rec_events_unapplied)
CREATE INDEX ix_content_deck_decisions_unapplied ON content_deck_decisions (org_id, applied);

UPDATE schema_version SET version = 80 WHERE version < 80;
