"""Entity CRUD helpers for sable.db."""
from __future__ import annotations

import sqlite3
import uuid

from sable_platform.errors import SableError, ENTITY_NOT_FOUND, ENTITY_ARCHIVED, ORG_NOT_FOUND

SHARED_HANDLE_MERGE_CONFIDENCE = 0.80


def create_entity(
    conn: sqlite3.Connection,
    org_id: str,
    display_name: str | None = None,
    status: str = "candidate",
    source: str = "auto",
) -> str:
    row = conn.execute("SELECT 1 FROM orgs WHERE org_id=?", (org_id,)).fetchone()
    if not row:
        raise SableError(ORG_NOT_FOUND, f"Org '{org_id}' not found")

    entity_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO entities (entity_id, org_id, display_name, status, source, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        """,
        (entity_id, org_id, display_name, status, source),
    )
    conn.commit()
    return entity_id


def find_entity_by_handle(
    conn: sqlite3.Connection,
    org_id: str,
    platform: str,
    handle: str,
) -> sqlite3.Row | None:
    handle = handle.lower().lstrip("@")
    return conn.execute(
        """
        SELECT e.entity_id, e.org_id, e.display_name, e.status, e.source
        FROM entities e
        JOIN entity_handles h ON e.entity_id = h.entity_id
        WHERE e.org_id = ?
          AND h.platform = ?
          AND h.handle = ?
          AND e.status != 'archived'
        """,
        (org_id, platform, handle),
    ).fetchone()


def get_entity(conn: sqlite3.Connection, entity_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
    if not row:
        raise SableError(ENTITY_NOT_FOUND, f"Entity '{entity_id}' not found")
    return row


def update_display_name(
    conn: sqlite3.Connection,
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
        "UPDATE entities SET display_name=?, updated_at=datetime('now') WHERE entity_id=?",
        (display_name, entity_id),
    )
    conn.commit()


def add_handle(
    conn: sqlite3.Connection,
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
        """
        SELECT h.entity_id
        FROM entity_handles h
        JOIN entities e ON h.entity_id = e.entity_id
        WHERE h.platform = ?
          AND h.handle = ?
          AND e.org_id = ?
          AND h.entity_id != ?
          AND e.status != 'archived'
        """,
        (platform, handle, org_id, entity_id),
    ).fetchone()

    try:
        conn.execute(
            """
            INSERT INTO entity_handles (entity_id, platform, handle, is_primary)
            VALUES (?, ?, ?, ?)
            """,
            (entity_id, platform, handle, 1 if is_primary else 0),
        )
    except sqlite3.IntegrityError:
        pass

    conn.execute(
        "UPDATE entities SET updated_at=datetime('now') WHERE entity_id=?",
        (entity_id,),
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
    conn: sqlite3.Connection,
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
        """
        INSERT INTO entity_notes (entity_id, body, source)
        VALUES (?, ?, ?)
        """,
        (entity_id, body, source),
    )
    conn.commit()
    return cur.lastrowid


def list_entity_notes(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    limit: int = 50,
) -> list[sqlite3.Row]:
    """List notes for an entity, newest first."""
    return conn.execute(
        "SELECT * FROM entity_notes WHERE entity_id=? ORDER BY created_at DESC, note_id DESC LIMIT ?",
        (entity_id, limit),
    ).fetchall()


def archive_entity(conn: sqlite3.Connection, entity_id: str) -> None:
    row = get_entity(conn, entity_id)
    conn.execute(
        "UPDATE entities SET status='archived', updated_at=datetime('now') WHERE entity_id=?",
        (entity_id,),
    )
    conn.commit()
    from sable_platform.db.audit import log_audit
    log_audit(conn, "system", "entity_archive",
              org_id=row["org_id"], entity_id=entity_id, source="system")
