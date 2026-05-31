"""media_assets registry — the shared home for media created/surfaced across tools.

register_asset() is idempotent on (org_id, r2_ref): re-registering the same object
upserts mutable fields rather than duplicating. Callers pass a live connection
(``sable_platform.db.connection.get_db()``); writes commit before returning.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from sqlalchemy import text

log = logging.getLogger("sable_platform.media.registry")


def register_asset(
    conn,
    org_id: str,
    source_project: str,
    kind: str,
    r2_ref: str,
    *,
    mime: Optional[str] = None,
    bytes: Optional[int] = None,
    sha256: Optional[str] = None,
    entity_id: Optional[str] = None,
    content_item_id: Optional[str] = None,
    source_ref: Optional[str] = None,
    caption: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> str:
    """Insert-or-update a media asset. Returns the asset_id.

    Idempotency key is (org_id, r2_ref). On conflict, mutable fields are updated;
    asset_id/created_at are preserved.
    """
    asset_id = uuid.uuid4().hex
    params = {
        "asset_id": asset_id,
        "org_id": org_id,
        "source_project": source_project,
        "kind": kind,
        "r2_ref": r2_ref,
        "mime": mime,
        "bytes": bytes,
        "sha256": sha256,
        "entity_id": entity_id,
        "content_item_id": content_item_id,
        "source_ref": source_ref,
        "caption": caption,
        "metadata_json": json.dumps(metadata or {}),
    }
    conn.execute(
        text(
            "INSERT INTO media_assets"
            " (asset_id, org_id, source_project, kind, r2_ref, mime, bytes,"
            "  sha256, entity_id, content_item_id, source_ref, caption, metadata_json)"
            " VALUES (:asset_id, :org_id, :source_project, :kind, :r2_ref, :mime,"
            "  :bytes, :sha256, :entity_id, :content_item_id, :source_ref, :caption,"
            "  :metadata_json)"
            " ON CONFLICT (org_id, r2_ref) DO UPDATE SET"
            "  kind = excluded.kind,"
            "  mime = excluded.mime,"
            "  bytes = excluded.bytes,"
            "  sha256 = excluded.sha256,"
            "  entity_id = excluded.entity_id,"
            "  content_item_id = excluded.content_item_id,"
            "  source_ref = excluded.source_ref,"
            "  caption = excluded.caption,"
            "  metadata_json = excluded.metadata_json"
        ),
        params,
    )
    conn.commit()
    # Return the canonical asset_id for this ref (the INSERT id on a fresh row,
    # or the pre-existing one on conflict).
    row = conn.execute(
        text("SELECT asset_id FROM media_assets WHERE org_id = :o AND r2_ref = :r"),
        {"o": org_id, "r": r2_ref},
    ).fetchone()
    return row[0] if row else asset_id


def find_by_sha(conn, org_id: str, sha256: str) -> Optional[str]:
    """Best-effort: return an existing r2_ref for identical content (re-upload
    avoidance). Sequential-use only — not a concurrency guarantee."""
    if not sha256:
        return None
    row = conn.execute(
        text(
            "SELECT r2_ref FROM media_assets"
            " WHERE org_id = :o AND sha256 = :s LIMIT 1"
        ),
        {"o": org_id, "s": sha256},
    ).fetchone()
    return row[0] if row else None


def get_asset(conn, org_id: str, r2_ref: str) -> Optional[dict]:
    row = conn.execute(
        text("SELECT * FROM media_assets WHERE org_id = :o AND r2_ref = :r"),
        {"o": org_id, "r": r2_ref},
    ).fetchone()
    return dict(row._mapping) if row else None


def list_assets(conn, org_id: str, kind: Optional[str] = None, limit: int = 100) -> list[dict]:
    if kind:
        rows = conn.execute(
            text(
                "SELECT * FROM media_assets WHERE org_id = :o AND kind = :k"
                " ORDER BY created_at DESC LIMIT :lim"
            ),
            {"o": org_id, "k": kind, "lim": limit},
        ).fetchall()
    else:
        rows = conn.execute(
            text(
                "SELECT * FROM media_assets WHERE org_id = :o"
                " ORDER BY created_at DESC LIMIT :lim"
            ),
            {"o": org_id, "lim": limit},
        ).fetchall()
    return [dict(r._mapping) for r in rows]
