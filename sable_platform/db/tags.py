"""Entity tag helpers for sable.db."""
from __future__ import annotations

import sqlite3
import uuid

_REPLACE_CURRENT_TAGS: frozenset[str] = frozenset({
    "high_lift_account",
    "top_contributor",
    "team_member",
    "cabal_member",
    "watchlist_account",
    "bd_prospect",
})

_ACTIVE_PREDICATE = "is_current = 1 AND (expires_at IS NULL OR expires_at > datetime('now'))"


def _record_tag_history(
    conn: sqlite3.Connection,
    entity_id: str,
    org_id: str,
    change_type: str,
    tag: str,
    *,
    confidence: float | None = None,
    source: str | None = None,
    source_ref: str | None = None,
    expires_at: str | None = None,
) -> None:
    """Write a row to entity_tag_history. No-op if table doesn't exist yet."""
    try:
        conn.execute(
            """
            INSERT INTO entity_tag_history
                (history_id, entity_id, org_id, change_type, tag, confidence,
                 source, source_ref, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (uuid.uuid4().hex, entity_id, org_id, change_type, tag,
             confidence, source, source_ref, expires_at),
        )
    except Exception:
        pass  # table absent before migration 008 — safe to skip


def _get_org_id(conn: sqlite3.Connection, entity_id: str) -> str | None:
    row = conn.execute("SELECT org_id FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
    return row["org_id"] if row else None


def add_tag(
    conn: sqlite3.Connection,
    entity_id: str,
    tag: str,
    source: str | None = None,
    confidence: float = 1.0,
    expires_at: str | None = None,
) -> None:
    org_id = _get_org_id(conn, entity_id) or ""

    if tag in _REPLACE_CURRENT_TAGS:
        # Record 'replaced' history for any existing active tag before deactivating
        existing = conn.execute(
            f"""
            SELECT confidence, source, expires_at FROM entity_tags
            WHERE entity_id = ? AND tag = ? AND {_ACTIVE_PREDICATE}
            """,
            (entity_id, tag),
        ).fetchone()
        if existing:
            _record_tag_history(
                conn, entity_id, org_id, "replaced", tag,
                confidence=existing["confidence"],
                source=existing["source"],
                expires_at=existing["expires_at"],
            )
        conn.execute(
            f"""
            UPDATE entity_tags
            SET is_current = 0, deactivated_at = datetime('now')
            WHERE entity_id = ? AND tag = ? AND {_ACTIVE_PREDICATE}
            """,
            (entity_id, tag),
        )

    _record_tag_history(
        conn, entity_id, org_id, "added", tag,
        confidence=confidence, source=source, expires_at=expires_at,
    )

    conn.execute(
        """
        INSERT INTO entity_tags (entity_id, tag, source, confidence, is_current, expires_at)
        VALUES (?, ?, ?, ?, 1, ?)
        """,
        (entity_id, tag, source, confidence, expires_at),
    )
    conn.execute(
        "UPDATE entities SET updated_at=datetime('now') WHERE entity_id=?",
        (entity_id,),
    )
    conn.commit()


def deactivate_tag(
    conn: sqlite3.Connection,
    entity_id: str,
    tag: str,
    reason: str = "expired",
    source: str | None = None,
) -> bool:
    """Deactivate an active tag on an entity. Returns True if a tag was deactivated.

    Records the change in entity_tag_history for audit trail.
    """
    org_id = _get_org_id(conn, entity_id) or ""

    existing = conn.execute(
        f"""
        SELECT confidence, source, expires_at FROM entity_tags
        WHERE entity_id = ? AND tag = ? AND {_ACTIVE_PREDICATE}
        """,
        (entity_id, tag),
    ).fetchone()
    if not existing:
        return False

    _record_tag_history(
        conn, entity_id, org_id, reason, tag,
        confidence=existing["confidence"],
        source=existing["source"],
        source_ref=source,
        expires_at=existing["expires_at"],
    )

    conn.execute(
        f"""
        UPDATE entity_tags
        SET is_current = 0, deactivated_at = datetime('now')
        WHERE entity_id = ? AND tag = ? AND {_ACTIVE_PREDICATE}
        """,
        (entity_id, tag),
    )
    conn.execute(
        "UPDATE entities SET updated_at=datetime('now') WHERE entity_id=?",
        (entity_id,),
    )
    conn.commit()
    return True


def get_active_tags(conn: sqlite3.Connection, entity_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        f"""
        SELECT * FROM entity_tags
        WHERE entity_id = ? AND {_ACTIVE_PREDICATE}
        ORDER BY added_at
        """,
        (entity_id,),
    ).fetchall()


def get_entities_by_tag(
    conn: sqlite3.Connection,
    org_id: str,
    tag: str,
) -> list[sqlite3.Row]:
    return conn.execute(
        f"""
        SELECT DISTINCT e.entity_id, e.display_name, e.status, e.org_id
        FROM entities e
        JOIN entity_tags t ON e.entity_id = t.entity_id
        WHERE e.org_id = ?
          AND t.tag = ?
          AND {_ACTIVE_PREDICATE.replace('is_current', 't.is_current')
               .replace('expires_at', 't.expires_at')}
          AND e.status != 'archived'
        """,
        (org_id, tag),
    ).fetchall()
