"""Entity merge helpers for sable.db."""
from __future__ import annotations

import datetime
import json
import sqlite3

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.errors import SableError, CROSS_ORG_MERGE_BLOCKED, ENTITY_NOT_FOUND
from sable_platform.db.tags import _REPLACE_CURRENT_TAGS

MERGE_CONFIDENCE_THRESHOLD = 0.70


def reconsider_expired_merges(org_id: str, conn: Connection, threshold: float = MERGE_CONFIDENCE_THRESHOLD) -> int:
    """Flip expired merge candidates back to pending if confidence now meets threshold."""
    cursor = conn.execute(
        text(
            "UPDATE merge_candidates SET status='pending'"
            " WHERE status='expired' AND confidence >= :threshold"
            "   AND entity_a_id IN (SELECT entity_id FROM entities WHERE org_id=:org_id)"
        ),
        {"threshold": threshold, "org_id": org_id},
    )
    conn.commit()
    return cursor.rowcount


def create_merge_candidate(
    conn: Connection,
    entity_a_id: str,
    entity_b_id: str,
    confidence: float = 0.0,
    reason: str | None = None,
) -> None:
    if entity_a_id > entity_b_id:
        entity_a_id, entity_b_id = entity_b_id, entity_a_id

    status = "expired" if confidence < MERGE_CONFIDENCE_THRESHOLD else "pending"

    conn.execute(
        text(
            "INSERT OR IGNORE INTO merge_candidates (entity_a_id, entity_b_id, confidence, reason, status)"
            " VALUES (:entity_a_id, :entity_b_id, :confidence, :reason, :status)"
        ),
        {
            "entity_a_id": entity_a_id,
            "entity_b_id": entity_b_id,
            "confidence": confidence,
            "reason": reason,
            "status": status,
        },
    )
    conn.commit()


def get_pending_merges(conn: Connection, org_id: str) -> list:
    return conn.execute(
        text(
            "SELECT mc.*"
            " FROM merge_candidates mc"
            " JOIN entities ea ON mc.entity_a_id = ea.entity_id"
            " WHERE ea.org_id = :org_id"
            "   AND mc.status = 'pending'"
            " ORDER BY mc.confidence DESC"
        ),
        {"org_id": org_id},
    ).fetchall()


def execute_merge(
    conn: Connection,
    source_entity_id: str,
    target_entity_id: str,
    merged_by: str | None = None,
    candidate_id: int | None = None,
) -> None:
    """Merge source entity into target entity (9-step single transaction)."""
    source_row = conn.execute(
        text("SELECT * FROM entities WHERE entity_id=:entity_id"),
        {"entity_id": source_entity_id},
    ).fetchone()
    target_row = conn.execute(
        text("SELECT * FROM entities WHERE entity_id=:entity_id"),
        {"entity_id": target_entity_id},
    ).fetchone()

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

    try:
        snapshot = {
            "source": dict(source_row),
            "target": dict(target_row),
            "merged_at": ts,
        }

        source_handles = conn.execute(
            text("SELECT * FROM entity_handles WHERE entity_id=:entity_id"),
            {"entity_id": source_entity_id},
        ).fetchall()
        for h in source_handles:
            existing = conn.execute(
                text(
                    "SELECT 1 FROM entity_handles"
                    " WHERE entity_id=:entity_id AND platform=:platform AND handle=:handle"
                ),
                {"entity_id": target_entity_id, "platform": h["platform"], "handle": h["handle"]},
            ).fetchone()
            if existing:
                conn.execute(
                    text(
                        "DELETE FROM entity_handles"
                        " WHERE entity_id=:entity_id AND platform=:platform AND handle=:handle"
                    ),
                    {"entity_id": source_entity_id, "platform": h["platform"], "handle": h["handle"]},
                )
            else:
                conn.execute(
                    text("UPDATE entity_handles SET entity_id=:target_id WHERE handle_id=:handle_id"),
                    {"target_id": target_entity_id, "handle_id": h["handle_id"]},
                )

        source_tags = conn.execute(
            text("SELECT * FROM entity_tags WHERE entity_id=:entity_id"),
            {"entity_id": source_entity_id},
        ).fetchall()
        for t in source_tags:
            if t["tag"] in _REPLACE_CURRENT_TAGS and t["is_current"]:
                conn.execute(
                    text("UPDATE entity_tags SET is_current=0, deactivated_at=:ts WHERE tag_id=:tag_id"),
                    {"ts": ts, "tag_id": t["tag_id"]},
                )
            else:
                conn.execute(
                    text("UPDATE entity_tags SET entity_id=:target_id WHERE tag_id=:tag_id"),
                    {"target_id": target_entity_id, "tag_id": t["tag_id"]},
                )

        conn.execute(
            text("UPDATE content_items SET entity_id=:target_id WHERE entity_id=:source_id"),
            {"target_id": target_entity_id, "source_id": source_entity_id},
        )

        source_notes = conn.execute(
            text("SELECT body FROM entity_notes WHERE entity_id=:entity_id ORDER BY created_at"),
            {"entity_id": source_entity_id},
        ).fetchall()
        if source_notes:
            merged_body = f"\n\n---\nMerged from {source_entity_id} at {ts}\n\n" + "\n".join(
                n["body"] for n in source_notes
            )
            conn.execute(
                text("INSERT INTO entity_notes (entity_id, body, source) VALUES (:entity_id, :body, 'merge')"),
                {"entity_id": target_entity_id, "body": merged_body},
            )

        conn.execute(
            text("UPDATE entities SET status='archived', updated_at=:ts WHERE entity_id=:entity_id"),
            {"ts": ts, "entity_id": source_entity_id},
        )
        conn.execute(
            text("UPDATE entities SET updated_at=:ts WHERE entity_id=:entity_id"),
            {"ts": ts, "entity_id": target_entity_id},
        )

        conn.execute(
            text(
                "INSERT INTO merge_events (source_entity_id, target_entity_id, candidate_id, merged_by, snapshot_json)"
                " VALUES (:source_entity_id, :target_entity_id, :candidate_id, :merged_by, :snapshot_json)"
            ),
            {
                "source_entity_id": source_entity_id,
                "target_entity_id": target_entity_id,
                "candidate_id": candidate_id,
                "merged_by": merged_by,
                "snapshot_json": json.dumps(snapshot),
            },
        )

        if candidate_id is not None:
            conn.execute(
                text("UPDATE merge_candidates SET status='merged', updated_at=:ts WHERE candidate_id=:candidate_id"),
                {"ts": ts, "candidate_id": candidate_id},
            )

        conn.commit()

        from sable_platform.db.audit import log_audit
        log_audit(conn, merged_by or "system", "entity_merge",
                  org_id=source_row["org_id"],
                  entity_id=target_entity_id,
                  detail={"source_entity_id": source_entity_id,
                          "target_entity_id": target_entity_id},
                  source="system")

    except Exception:
        conn.rollback()
        raise
