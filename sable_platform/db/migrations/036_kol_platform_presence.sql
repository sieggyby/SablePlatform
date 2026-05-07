-- Migration 036: cross-platform presence column for kol_candidates
-- Stores Instagram, TikTok, Threads, YouTube etc handle and follower data
-- as a JSON blob. Lets the matcher score across platforms (e.g. IG matters more
-- than X for fashion, TikTok matters more for streetwear etc)
--
-- Shape of platform_presence_json:
-- {"instagram": {"handle": str, "followers": int, "verified": bool, "fetched_at": iso8601},
--  "tiktok":    {"handle": str, "followers": int, "verified": bool, "fetched_at": iso8601},
--  "threads":   {...}, "youtube": {...}, ...}

ALTER TABLE kol_candidates ADD COLUMN platform_presence_json TEXT NOT NULL DEFAULT '{}';
