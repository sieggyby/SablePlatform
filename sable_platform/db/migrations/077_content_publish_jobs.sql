-- 077_content_publish_jobs.sql
-- Content Deck Phase 4 -- the keep->schedule->release substrate ("the missing organ").
-- A KEPT candidate is SCHEDULED into a content_publish_job. A claim-due worker flips the job to
-- 'due' at publish_at and surfaces it for OPERATOR HAND-OFF (composeUrl + media download) --
-- there is NO auto-publish-to-X in v1 (a real X transport is gated behind Phase 6 autonomy and
-- must NOT reuse the TG/Discord outbox). The candidate's OWN status flips kept->scheduled->posted
-- (already allowed by the 076 content_candidates status CHECK) -- the worker lifecycle lives in
-- release_state HERE so the candidate status CHECK is not overloaded with worker state
-- (Codex-r1 CRITICAL: 'due' is release-worker state, not a candidate status).
--
-- Design (masterplan Phase 4):
--   * release_state enum scheduled|due|claimed|handed_off|posted|canceled -- the worker lifecycle.
--   * publish_at = when to release. The claim-due worker future-gates on publish_at <= now
--     (mirrors the relay publication-job claim pattern), single-flight via an atomic UPDATE.
--   * candidate_id FK -> content_candidates ON DELETE CASCADE (a job dies with its candidate --
--     candidates SOFT-expire in normal operation, so the FK holds -- a GC hard-delete cascades).
--   * org_id REFERENCES orgs(org_id) -- the scope wall. NO cost column, ever.
--   * target_handle NOT NULL -- a null-target candidate cannot be scheduled (masterplan SEC-3).
--   * STALE GUARD (accessor-enforced, asserted in tests): a SCHEDULED candidate past its ORIGINAL
--     expires_at STILL releases at publish_at (expire_due_candidates is pending-only) -- the release
--     path skips a since-REJECTED candidate.
--   * publish_at STRICT-UTC FORMAT (DB backstop, Codex Tier-2): the claim-due worker compares
--     publish_at LEXICALLY against the second-precision Z form, so an offset (+02:00), naive (no
--     zone), space-separated, compact, or fractional value would release early or never release.
--     The Slopper route + schedule_candidate() already validate this, but a DIRECT writer/backfill
--     could store a malformed value -- the CHECK below rejects any non-canonical shape at the DB.
--     It enforces SHAPE (YYYY-MM-DDTHH:MM:SSZ via GLOB digit-classes), NOT calendar validity: a
--     shaped-but-impossible month/day (2099-13-01T...) still passes the GLOB and is caught by the
--     accessor's strptime (kept there per the Tier-2 finding -- SQLite GLOB cannot range-check).
-- Comment hygiene: NO semicolons inside double-dash comment lines (the runner splits on the char).

CREATE TABLE content_publish_jobs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id    INTEGER NOT NULL REFERENCES content_candidates (id) ON DELETE CASCADE,
  org_id          TEXT NOT NULL REFERENCES orgs (org_id),
  target_handle   TEXT NOT NULL,
  release_state   TEXT NOT NULL DEFAULT 'scheduled',
  publish_at      TEXT NOT NULL,
  next_attempt_at TEXT,
  attempt_count   INTEGER NOT NULL DEFAULT 0,
  claimed_at      TEXT,
  handed_off_at   TEXT,
  posted_ref      TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  CHECK (release_state IN ('scheduled', 'due', 'claimed', 'handed_off', 'posted', 'canceled')),
  CONSTRAINT ck_content_publish_jobs_publish_at_utc CHECK (
    publish_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
  )
);

CREATE INDEX content_publish_jobs_by_org_state ON content_publish_jobs (org_id, release_state, publish_at);
CREATE INDEX content_publish_jobs_due ON content_publish_jobs (release_state, publish_at);
CREATE INDEX content_publish_jobs_by_candidate ON content_publish_jobs (candidate_id);

UPDATE schema_version SET version = 77 WHERE version < 77;
