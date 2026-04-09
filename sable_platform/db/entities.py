"""Entity CRUD helpers for sable.db."""
from __future__ import annotations

import sqlite3
import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError as SAIntegrityError

from sable_platform.errors import SableError, ENTITY_NOT_FOUND, ENTITY_ARCHIVED, ORG_NOT_FOUND

SHARED_HANDLE_MERGE_CONFIDENCE = 0.80


def create_entity(
    conn: Connection,
    org_id: str,
    display_name: str | None = None,
    status: str = "candidate",
    source: str = "auto",
) -> str:
    row = conn.execute(text("SELECT 1 FROM orgs WHERE org_id=:org_id"), {"org_id": org_id}).fetchone()
    if not row:
        raise SableError(ORG_NOT_FOUND, f"Org '{org_id}' not found")

    entity_id = uuid.uuid4().hex
    conn.execute(
        text("""
        INSERT INTO entities (entity_id, org_id, display_name, status, source, updated_at)
        VALUES (:entity_id, :org_id, :display_name, :status, :source, CURRENT_TIMESTAMP)
        """),
        {"entity_id": entity_id, "org_id": org_id, "display_name": display_name,
         "status": status, "source": source},
    )
    conn.commit()
    return entity_id


def find_entity_by_handle(
    conn: Connection,
    org_id: str,
    platform: str,
    handle: str,
) -> dict | None:
    handle = handle.lower().lstrip("@")
    return conn.execute(
        text("""
        SELECT e.entity_id, e.org_id, e.display_name, e.status, e.source
        FROM entities e
        JOIN entity_handles h ON e.entity_id = h.entity_id
        WHERE e.org_id = :org_id
          AND h.platform = :platform
          AND h.handle = :handle
          AND e.status != 'archived'
        """),
        {"org_id": org_id, "platform": platform, "handle": handle},
    ).fetchone()


def get_entity(conn: Connection, entity_id: str):
    row = conn.execute(text("SELECT * FROM entities WHERE entity_id=:entity_id"), {"entity_id": entity_id}).fetchone()
    if not row:
        raise SableError(ENTITY_NOT_FOUND, f"Entity '{entity_id}' not found")
    return row


def update_display_name(
    conn: Connection,
    entity_id: str,
    display_name: str,
    source: str = "auto",
) -> None:
    row = get_entity(conn, entity_id)
    if row["status"] == "archived":
        raise SableError(ENTITY_ARCHIVED, f"Entity '{entity_id}' is archived")
    if row["status"] == "confirmed" and source != "manual":
        return
    conn.execute(
        text("UPDATE entities SET display_name=:display_name, updated_at=CURRENT_TIMESTAMP WHERE entity_id=:entity_id"),
        {"display_name": display_name, "entity_id": entity_id},
    )
    conn.commit()


def add_handle(
    conn: Connection,
    entity_id: str,
    platform: str,
    handle: str,
    is_primary: bool = False,
) -> None:
    handle = handle.lower().lstrip("@")

    row = get_entity(conn, entity_id)
    if row["status"] == "archived":
        raise SableError(ENTITY_ARCHIVED, f"Entity '{entity_id}' is archived")

    org_id = row["org_id"]

    existing = conn.execute(
        text("""
        SELECT h.entity_id
        FROM entity_handles h
        JOIN entities e ON h.entity_id = e.entity_id
        WHERE h.platform = :platform
          AND h.handle = :handle
          AND e.org_id = :org_id
          AND h.entity_id != :entity_id
          AND e.status != 'archived'
        """),
        {"platform": platform, "handle": handle, "org_id": org_id, "entity_id": entity_id},
    ).fetchone()

    try:
        conn.execute(
            text("""
            INSERT INTO entity_handles (entity_id, platform, handle, is_primary)
            VALUES (:entity_id, :platform, :handle, :is_primary)
            """),
            {"entity_id": entity_id, "platform": platform, "handle": handle,
             "is_primary": 1 if is_primary else 0},
        )
    except (sqlite3.IntegrityError, SAIntegrityError):
        pass

    conn.execute(
        text("UPDATE entities SET updated_at=CURRENT_TIMESTAMP WHERE entity_id=:entity_id"),
        {"entity_id": entity_id},
    )
    conn.commit()

    if existing:
        from sable_platform.db.merge import create_merge_candidate

        create_merge_candidate(
            conn,
            existing["entity_id"],
            entity_id,
            confidence=SHARED_HANDLE_MERGE_CONFIDENCE,
            reason=f"shared {platform} handle @{handle}",
        )


def add_entity_note(
    conn: Connection,
    entity_id: str,
    body: str,
    source: str = "manual",
) -> int:
    """Add a note to an entity. Returns note_id.

    Raises SableError if entity does not exist or is archived.
    """
    row = get_entity(conn, entity_id)
    if row["status"] == "archived":
        raise SableError(ENTITY_ARCHIVED, f"Entity '{entity_id}' is archived")

    cur = conn.execute(
        text("""
        INSERT INTO entity_notes (entity_id, body, source)
        VALUES (:entity_id, :body, :source)
        """),
        {"entity_id": entity_id, "body": body, "source": source},
    )
    conn.commit()
    return cur.lastrowid


def list_entity_notes(
    conn: Connection,
    entity_id: str,
    *,
    limit: int = 50,
) -> list:
    """List notes for an entity, newest first."""
    return conn.execute(
        text("SELECT * FROM entity_notes WHERE entity_id=:entity_id ORDER BY created_at DESC, note_id DESC LIMIT :limit"),
        {"entity_id": entity_id, "limit": limit},
    ).fetchall()


def archive_entity(conn: Connection, entity_id: str) -> None:
    row = get_entity(conn, entity_id)
    conn.execute(
        text("UPDATE entities SET status='archived', updated_at=CURRENT_TIMESTAMP WHERE entity_id=:entity_id"),
        {"entity_id": entity_id},
    )
    conn.commit()
    from sable_platform.db.audit import log_audit
    log_audit(conn, "system", "entity_archive",
              org_id=row["org_id"], entity_id=entity_id, source="system")
