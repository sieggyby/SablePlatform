-- 085_quality_media_reply.sql
-- Media + reply metadata on the fixed-age quality corpus (K1 instrumentation ask from
-- the 2026-07-08 kill-test re-run) -- the June "media is the #1 lever" finding could not
-- be re-tested because the corpus carried no media flag, and is_reply had to be derived
-- from leading @s. The Slopper quality tap parses these from the SocialData raw object
-- at ingest -- a one-shot backfill parses the retained relay_tweets.raw for pre-085 rows.
--
-- Column semantics (three-valued on purpose) --
--   media_kinds       NULL = not yet parsed (pre-backfill row), '' = parsed and NO media,
--                     else a sorted comma list of entity types, e.g. 'photo' or
--                     'animated_gif,video'
--   is_reply          NULL = not yet parsed, 0/1 = parsed verdict
--   in_reply_to_x_id  the parent tweet's X id, set only when is_reply = 1 and the id
--                     was present in the raw object
--   SPECIAL SIGNATURE the backfill stamps (media_kinds = '' AND is_reply IS NULL) on a
--                     row whose cache raw was missing/unparseable -- distinguishable
--                     from live/parsed rows (those always carry is_reply 0/1)
-- 100% additive. Comment hygiene: no semicolons inside these comment lines.

ALTER TABLE relay_quality_tweets ADD COLUMN media_kinds TEXT;
ALTER TABLE relay_quality_tweets ADD COLUMN is_reply INTEGER;
ALTER TABLE relay_quality_tweets ADD COLUMN in_reply_to_x_id TEXT;

UPDATE schema_version SET version = 85 WHERE version < 85;
