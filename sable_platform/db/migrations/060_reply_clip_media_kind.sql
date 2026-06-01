-- 060_reply_clip_media_kind.sql
-- Adds reply_suggestions.clip_media_kind: the media kind (image / video / none)
-- a generated reply attached. Backs the operator reply-assist prefer-image
-- ranking + per-operator anti-spam image throttle in Slopper /reply -- the
-- system prefers image clips (they out-earn video per the CT best-practices
-- analysis) but turns reluctant once an operator has been recommended images
-- more than a threshold within a rolling window (counted from this column).
-- Nullable -- pre-existing rows and text-only / clip-less replies stay NULL.

ALTER TABLE reply_suggestions ADD COLUMN clip_media_kind TEXT;

UPDATE schema_version SET version = 60 WHERE version < 60;
