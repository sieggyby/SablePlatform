-- 063_reply_learning.sql
-- Reply-Opportunity Feed P3 learning maturity (REPLY_OPPORTUNITY_FEED_PLAN.md
-- section 6 / section 8 P3 / section 10.4). Persists the section-10 anti-AI-tell
-- tell-score + tell-flags on reply_suggestions for the quality dashboard (the
-- section-10.4 "its own dashboard/migration" deferral), and caches the P3
-- embedding ranker vector on relay_tweets so a candidate is not re-embedded each
-- sweep. 100 percent ADDITIVE: ADD COLUMN only -- same discipline as migration
-- 062. NO table rebuild (the runner wraps each file in one transaction with
-- foreign_keys ON, so a DROP TABLE would raise on FK-d child rows), NO CHECK
-- change, NO NOT-NULL relax. All four columns are nullable with no default.
--
-- Comment hygiene: no semicolons inside double-dash comment lines (the runner in
-- connection.py splits on the literal semicolon, and is version-gated so it only
-- replays a file when current schema_version is below the target).

-- reply_suggestions (056/062): persist the section-10 humanizer signals so the
-- section-10.4/section-6 quality dashboard + guardrail-refinement proposals can
-- read them. tell_score is the 0..1 weighted flag-density. tell_flags_json is the
-- flags blob ({type, span, why} list). Both NULL for pre-063 / unlinted rows.
ALTER TABLE reply_suggestions ADD COLUMN tell_score REAL;
ALTER TABLE reply_suggestions ADD COLUMN tell_flags_json TEXT;

-- relay_tweets (057/062): cache the P3 ranker embedding so a candidate is
-- embedded once, not every sweep. embedding_json is the vector blob, and
-- embedding_model records which provider/model produced it (so a model swap
-- invalidates correctly).
ALTER TABLE relay_tweets ADD COLUMN embedding_json TEXT;
ALTER TABLE relay_tweets ADD COLUMN embedding_model TEXT;

UPDATE schema_version SET version = 63 WHERE version < 63;
