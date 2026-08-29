"""Entity tag helpers for sable.db."""
from __future__ import annotations

import logging
import sqlite3
import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError as SAOperationalError

from sable_platform.db.audit import log_audit
from sable_platform.db.compat import get_dialect
from sable_platform.db.ts_format import now_canonical_sql

log = logging.getLogger(__name__)

_REPLACE_CURRENT_TAGS: frozenset[str] = frozenset({
    "high_lift_account",
    "top_contributor",
    "team_member",
    "cabal_member",
    "watchlist_account",
    "bd_prospect",
    "cultist_candidate",
    "bridge_node",
})

# Was: `expires_at > CAST(CURRENT_TIMESTAMP AS TEXT)`, a LEXICOGRAPHIC compare. Stored values
# mix formats: `datetime('now')` writes "YYYY-MM-DD HH:MM:SS" and later code writes ISO with a
# "T". "T" (0x54) sorts above " " (0x20), so an ISO value expiring EARLIER the same day compares
# as LATER and the tag stays active. Measured on PostgreSQL 16:
#     '2026-08-27T10:00:00' > '2026-08-27 23:00:00'  ->  true   (text)
#     the same values as timestamps                  ->  false
# Compare real timestamps instead, per dialect.
def active_predicate(dialect: str) -> str:
    """`is_current` plus a not-yet-expired check that compares TIMESTAMPS, not text."""
    from sable_platform.db.compat import ts_column

    if dialect == "sqlite":
        return ("is_current = 1 AND (expires_at IS NULL"
                " OR julianday(NULLIF(expires_at, '')) > julianday('now'))")
    return ("is_current = 1 AND (expires_at IS NULL"
            f" OR {ts_column('expires_at', dialect)} > NOW())")


def _record_tag_history(
    conn: Connection,
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
            text("""
            INSERT INTO entity_tag_history
                (history_id, entity_id, org_id, change_type, tag, confidence,
                 source, source_ref, expires_at)
            VALUES (:history_id, :entity_id, :org_id, :change_type, :tag, :confidence,
                    :source, :source_ref, :expires_at)
            """),
            {"history_id": uuid.uuid4().hex, "entity_id": entity_id, "org_id": org_id,
             "change_type": change_type, "tag": tag, "confidence": confidence,
             "source": source, "source_ref": source_ref, "expires_at": expires_at},
        )
    except (sqlite3.OperationalError, SAOperationalError) as exc:
        if "no such table" in str(exc):
            pass  # table absent before migration 008 — safe to skip
        else:
            log.warning("tag history write failed for entity %s: %s", entity_id, exc)
            raise


def _get_org_id(conn: Connection, entity_id: str) -> str | None:
    row = conn.execute(text("SELECT org_id FROM entities WHERE entity_id=:entity_id"), {"entity_id": entity_id}).fetchone()
    return row["org_id"] if row else None


def add_tag(
    conn: Connection,
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
            text(f"""
            SELECT confidence, source, expires_at FROM entity_tags
            WHERE entity_id = :entity_id AND tag = :tag AND {active_predicate(conn.dialect.name)}
            """),
            {"entity_id": entity_id, "tag": tag},
        ).fetchone()
        if existing:
            _record_tag_history(
                conn, entity_id, org_id, "replaced", tag,
                confidence=existing["confidence"],
                source=existing["source"],
                expires_at=existing["expires_at"],
            )
        conn.execute(
            text(f"""
            UPDATE entity_tags
            SET is_current = 0, deactivated_at = {now_canonical_sql(get_dialect(conn))}
            WHERE entity_id = :entity_id AND tag = :tag AND {active_predicate(conn.dialect.name)}
            """),
            {"entity_id": entity_id, "tag": tag},
        )

    _record_tag_history(
        conn, entity_id, org_id, "added", tag,
        confidence=confidence, source=source, expires_at=expires_at,
    )

    conn.execute(
        text("""
        INSERT INTO entity_tags (entity_id, tag, source, confidence, is_current, expires_at)
        VALUES (:entity_id, :tag, :source, :confidence, 1, :expires_at)
        """),
        {"entity_id": entity_id, "tag": tag, "source": source,
         "confidence": confidence, "expires_at": expires_at},
    )
    conn.execute(
        text(f"UPDATE entities SET updated_at={now_canonical_sql(get_dialect(conn))}"
             f" WHERE entity_id=:entity_id"),
        {"entity_id": entity_id},
    )
    conn.commit()


def deactivate_tag(
    conn: Connection,
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
        text(f"""
        SELECT confidence, source, expires_at FROM entity_tags
        WHERE entity_id = :entity_id AND tag = :tag AND {active_predicate(conn.dialect.name)}
        """),
        {"entity_id": entity_id, "tag": tag},
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
        text(f"""
        UPDATE entity_tags
        SET is_current = 0, deactivated_at = {now_canonical_sql(get_dialect(conn))}
        WHERE entity_id = :entity_id AND tag = :tag AND {active_predicate(conn.dialect.name)}
        """),
        {"entity_id": entity_id, "tag": tag},
    )
    conn.execute(
        text(f"UPDATE entities SET updated_at={now_canonical_sql(get_dialect(conn))}"
             f" WHERE entity_id=:entity_id"),
        {"entity_id": entity_id},
    )
    conn.commit()
    log_audit(conn, source or "system", "tag_deactivate",
              entity_id=entity_id,
              detail={"tag": tag, "reason": reason}, source="system")
    return True


def get_active_tags(conn: Connection, entity_id: str) -> list:
    return conn.execute(
        text(f"""
        SELECT * FROM entity_tags
        WHERE entity_id = :entity_id AND {active_predicate(conn.dialect.name)}
        ORDER BY added_at
        """),
        {"entity_id": entity_id},
    ).fetchall()


def get_entities_by_tag(
    conn: Connection,
    org_id: str,
    tag: str,
) -> list:
    return conn.execute(
        text(f"""
        SELECT DISTINCT e.entity_id, e.display_name, e.status, e.org_id
        FROM entities e
        JOIN entity_tags t ON e.entity_id = t.entity_id
        WHERE e.org_id = :org_id
          AND t.tag = :tag
          AND {active_predicate(conn.dialect.name).replace('is_current', 't.is_current')
               .replace('expires_at', 't.expires_at')}
          AND e.status != 'archived'
        """),
        {"org_id": org_id, "tag": tag},
    ).fetchall()
