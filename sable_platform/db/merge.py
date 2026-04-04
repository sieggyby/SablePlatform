"""Entity merge helpers for sable.db."""
from __future__ import annotations

import datetime
import json
import sqlite3

from sable_platform.errors import SableError, CROSS_ORG_MERGE_BLOCKED, ENTITY_NOT_FOUND
from sable_platform.db.tags import _REPLACE_CURRENT_TAGS

MERGE_CONFIDENCE_THRESHOLD = 0.70


def reconsider_expired_merges(org_id: str, conn: sqlite3.Connection, threshold: float = MERGE_CONFIDENCE_THRESHOLD) -> int:
    """Flip expired merge candidates back to pending if confidence now meets threshold."""
    cursor = conn.execute(
        """UPDATE merge_candidates SET status='pending'
           WHERE status='expired' AND confidence >= ?
             AND entity_a_id IN (SELECT entity_id FROM entities WHERE org_id=?)""",
        (threshold, org_id)
    )
    conn.commit()
    return cursor.rowcount


def create_merge_candidate(
    conn: sqlite3.Connection,
    entity_a_id: str,
    entity_b_id: str,
    confidence: float = 0.0,
    reason: str | None = None,
) -> None:
    if entity_a_id > entity_b_id:
        entity_a_id, entity_b_id = entity_b_id, entity_a_id

    status = "expired" if confidence < MERGE_CONFIDENCE_THRESHOLD else "pending"

    conn.execute(
        """
        INSERT OR IGNORE INTO merge_candidates (entity_a_id, entity_b_id, confidence, reason, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entity_a_id, entity_b_id, confidence, reason, status),
    )
    conn.commit()


def get_pending_merges(conn: sqlite3.Connection, org_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT mc.*
        FROM merge_candidates mc
        JOIN entities ea ON mc.entity_a_id = ea.entity_id
        WHERE ea.org_id = ?
          AND mc.status = 'pending'
        ORDER BY mc.confidence DESC
        """,
        (org_id,),
    ).fetchall()


def execute_merge(
    conn: sqlite3.Connection,
    source_entity_id: str,
    target_entity_id: str,
    merged_by: str | None = None,
    candidate_id: int | None = None,
) -> None:
    """Merge source entity into target entity (9-step single transaction)."""
    source_row = conn.execute("SELECT * FROM entities WHERE entity_id=?", (source_entity_id,)).fetchone()
    target_row = conn.execute("SELECT * FROM entities WHERE entity_id=?", (target_entity_id,)).fetchone()

    if source_row is None:
        raise SableError(ENTITY_NOT_FOUND, f"Source entity {source_entity_id!r} not found")
    if target_row is None:
        raise SableError(ENTITY_NOT_FOUND, f"Target entity {target_entity_id!r} not found")

    if source_row["org_id"] != target_row["org_id"]:
        raise SableError(
            CROSS_ORG_MERGE_BLOCKED,
            f"Cannot merge entities from different orgs: {source_row['org_id']} vs {target_row['org_id']}",
        )

    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()

    conn.execute("BEGIN")

    try:
        snapshot = {
            "source": dict(source_row),
            "target": dict(target_row),
            "merged_at": ts,
        }

        source_handles = conn.execute(
            "SELECT * FROM entity_handles WHERE entity_id=?", (source_entity_id,)
        ).fetchall()
        for h in source_handles:
            existing = conn.execute(
                "SELECT 1 FROM entity_handles WHERE entity_id=? AND platform=? AND handle=?",
                (target_entity_id, h["platform"], h["handle"]),
            ).fetchone()
            if existing:
                conn.execute(
                    "DELETE FROM entity_handles WHERE entity_id=? AND platform=? AND handle=?",
                    (source_entity_id, h["platform"], h["handle"]),
                )
            else:
                conn.execute(
                    "UPDATE entity_handles SET entity_id=? WHERE handle_id=?",
                    (target_entity_id, h["handle_id"]),
                )

        source_tags = conn.execute(
            "SELECT * FROM entity_tags WHERE entity_id=?", (source_entity_id,)
        ).fetchall()
        for t in source_tags:
            if t["tag"] in _REPLACE_CURRENT_TAGS and t["is_current"]:
                conn.execute(
                    "UPDATE entity_tags SET is_current=0, deactivated_at=? WHERE tag_id=?",
                    (ts, t["tag_id"]),
                )
            else:
                conn.execute(
                    "UPDATE entity_tags SET entity_id=? WHERE tag_id=?",
                    (target_entity_id, t["tag_id"]),
                )

        conn.execute(
            "UPDATE content_items SET entity_id=? WHERE entity_id=?",
            (target_entity_id, source_entity_id),
        )

        source_notes = conn.execute(
            "SELECT body FROM entity_notes WHERE entity_id=? ORDER BY created_at",
            (source_entity_id,),
        ).fetchall()
        if source_notes:
            merged_body = f"\n\n---\nMerged from {source_entity_id} at {ts}\n\n" + "\n".join(
                n["body"] for n in source_notes
            )
            conn.execute(
                "INSERT INTO entity_notes (entity_id, body, source) VALUES (?, ?, 'merge')",
                (target_entity_id, merged_body),
            )

        conn.execute(
            "UPDATE entities SET status='archived', updated_at=? WHERE entity_id=?",
            (ts, source_entity_id),
        )
        conn.execute(
            "UPDATE entities SET updated_at=? WHERE entity_id=?",
            (ts, target_entity_id),
        )

        conn.execute(
            """
            INSERT INTO merge_events (source_entity_id, target_entity_id, candidate_id, merged_by, snapshot_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (source_entity_id, target_entity_id, candidate_id, merged_by, json.dumps(snapshot)),
        )

        if candidate_id is not None:
            conn.execute(
                "UPDATE merge_candidates SET status='merged', updated_at=? WHERE candidate_id=?",
                (ts, candidate_id),
            )

        conn.execute("COMMIT")

        from sable_platform.db.audit import log_audit
        log_audit(conn, merged_by or "system", "entity_merge",
                  org_id=source_row["org_id"],
                  entity_id=target_entity_id,
                  detail={"source_entity_id": source_entity_id,
                          "target_entity_id": target_entity_id},
                  source="system")

    except Exception:
        conn.execute("ROLLBACK")
        raise
