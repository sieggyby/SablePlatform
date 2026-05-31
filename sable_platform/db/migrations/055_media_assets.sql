-- 055_media_assets.sql
-- Shared media-asset registry: the cross-tool home for media created in
-- SableSlopper (clips/cards/brainrot/memes) and surfaced in SableTracking
-- (contribution media). Holds the canonical R2 reference (bucket/key) plus
-- linkage and searchable caption text. See docs/SHARED_MEDIA_LAYER_PLAN_V1.md.
--
-- Comment hygiene: no semicolons inside double-dash comment lines. The runner
-- in connection.py splits on the literal semicolon character.

CREATE TABLE IF NOT EXISTS media_assets (
    asset_id        TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES orgs(org_id),
    source_project  TEXT NOT NULL,
    kind            TEXT NOT NULL,
    r2_ref          TEXT NOT NULL,
    mime            TEXT,
    bytes           INTEGER,
    sha256          TEXT,
    entity_id       TEXT REFERENCES entities(entity_id),
    content_item_id TEXT REFERENCES content_items(item_id),
    source_ref      TEXT,
    caption         TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_media_assets_org_ref ON media_assets(org_id, r2_ref);
CREATE INDEX IF NOT EXISTS ix_media_assets_org_kind ON media_assets(org_id, kind);
CREATE INDEX IF NOT EXISTS ix_media_assets_sha ON media_assets(org_id, sha256);

UPDATE schema_version SET version = 55 WHERE version < 55;
