-- 079_deck_assertion_nonce.sql
-- Single-use store for deck/produce authorization assertions (Codex Tier-1 replay defense).
--
-- The SableWeb-signed deck_authz assertion (sable/serve/deck_authz.py) is verified by Slopper before
-- every schedule/handoff/post/cancel/produce, but the HMAC binds only (action,id,org,handle,actor,
-- exp) and was REPLAYABLE until exp -- a captured-but-valid assertion could be re-POSTed within its
-- TTL to drive a repeated PAID meme ideation (bounded only by the weekly budget) or to re-fire a
-- state flip with mutated UNSIGNED request fields (publish_at / posted_ref / num|topic|band).
--
-- This table makes each assertion SINGLE-USE: Slopper consumes the assertion SIGNATURE (the HMAC
-- hex -- the most-signed value, unforgeable without the shared secret) exactly once, BEFORE any
-- budget reserve or state change. The PRIMARY KEY(sig) makes the consume race/replay-safe across
-- workers (an in-process cache is NOT sufficient): the FIRST POST carrying a given sig wins, every
-- later replay (even with tampered num/topic/band/publish_at/posted_ref) hits the unique constraint
-- and 403s. This closes both the replay and the replay-with-tampered-field vectors WITHOUT a
-- breaking HMAC payload version bump (the heavier jti+request_hash rollout stays deferred -- see
-- sable/serve/deck_authz.py). NO cost column, ever.
--
-- exp is stored (unix seconds) ONLY so expired rows can be GC'd via gc_expired_assertions -- the
-- verifier already rejected an expired/over-future assertion before the consume, so a consumed row is
-- always within its valid window at insert time.
-- Comment hygiene: NO semicolons inside double-dash comment lines (the runner splits on the char).

CREATE TABLE deck_consumed_assertions (
  sig          TEXT PRIMARY KEY,
  action       TEXT NOT NULL,
  org_id       TEXT NOT NULL,
  actor        TEXT NOT NULL,
  exp          INTEGER NOT NULL,
  consumed_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX deck_consumed_assertions_by_exp ON deck_consumed_assertions (exp);

UPDATE schema_version SET version = 79 WHERE version < 79;
