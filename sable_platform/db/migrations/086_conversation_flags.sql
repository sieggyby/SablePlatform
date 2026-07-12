-- 086_conversation_flags.sql
-- The Conversation Watcher's output table. A flag is one moment in a moderated community
-- chat (Discord or Telegram) that a heuristic scorer judged worth an operator pitching into,
-- posted to that client's topic in Sable's internal TG chat. This table is the durable
-- record + the dedupe/cooldown substrate + the feedback ledger that calibration reads.
--
-- Deliberately NOT relay_reply_opportunities (mig 057/062): that table FKs tweet_id ->
-- relay_tweets and has flagger_id NOT NULL -- it models a human flagging a TWEET, not an
-- automated scorer flagging a CONVERSATION. Different substrate, different lifecycle.
--
-- kind: 'opportunity' (pitch-worthy) vs 'brand_risk' (a member asserted something on the
-- client's guardrails.yaml forbidden_claims/do_not_mention list -- highest-value, own
-- cooldown, bypasses the burst gate). signals_json is the reason vector the scorer emits so
-- the flag explains itself with no LLM (S1 burst .. S8 help-density). Dedupe is APP-LEVEL over
-- non-terminal rows (no UNIQUE): the writer scopes a cooldown window per (platform, channel_id)
-- inside immediate_txn, mirroring relay/db.py find_active_opportunity_for_tweet. feedback is
-- the operator verdict ('pitched'|'noise') the precision gate reads. No cost column -- the
-- scorer is zero-LLM by design.
-- Comment hygiene: NO semicolons inside double-dash comment lines (the runner splits on the char).

CREATE TABLE community_conversation_flags (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id            TEXT NOT NULL REFERENCES orgs(org_id),
    platform          TEXT NOT NULL,
    space_id          TEXT NOT NULL,
    channel_id        TEXT NOT NULL,
    anchor_message_id TEXT NOT NULL,
    window_start      TEXT NOT NULL,
    window_end        TEXT NOT NULL,
    score             REAL NOT NULL,
    kind              TEXT NOT NULL DEFAULT 'opportunity',
    signals_json      TEXT NOT NULL DEFAULT '{}',
    reason            TEXT,
    status            TEXT NOT NULL DEFAULT 'active',
    feedback          TEXT,
    delivered_at      TEXT,
    expires_at        TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (platform IN ('discord', 'telegram')),
    CHECK (kind IN ('opportunity', 'brand_risk')),
    CHECK (status IN ('active', 'delivered', 'handled', 'noise', 'expired'))
);

CREATE INDEX ix_ccf_feed ON community_conversation_flags (org_id, status, created_at);
CREATE INDEX ix_ccf_cooldown ON community_conversation_flags (platform, channel_id, created_at);
CREATE INDEX ix_ccf_expiry ON community_conversation_flags (expires_at);

UPDATE schema_version SET version = 86 WHERE version < 86;
