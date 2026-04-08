"""Operator action helpers for sable.db."""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.errors import SableError, ENTITY_NOT_FOUND


def create_action(
    conn: Connection,
    org_id: str,
    title: str,
    *,
    source: str = "manual",
    action_type: str = "general",
    entity_id: str | None = None,
    content_item_id: str | None = None,
    source_ref: str | None = None,
    description: str | None = None,
) -> str:
    """Create a pending action. Returns action_id."""
    action_id = uuid.uuid4().hex
    conn.execute(
        text("""
        INSERT INTO actions
            (action_id, org_id, entity_id, content_item_id, source, source_ref,
             action_type, title, description)
        VALUES (:action_id, :org_id, :entity_id, :content_item_id, :source, :source_ref,
                :action_type, :title, :description)
        """),
        {"action_id": action_id, "org_id": org_id, "entity_id": entity_id,
         "content_item_id": content_item_id, "source": source, "source_ref": source_ref,
         "action_type": action_type, "title": title, "description": description},
    )
    conn.commit()
    return action_id


def claim_action(conn: Connection, action_id: str, operator: str) -> None:
    """Mark an action as claimed by an operator."""
    conn.execute(
        text("""
        UPDATE actions
        SET status='claimed', operator=:operator, claimed_at=datetime('now')
        WHERE action_id=:action_id
        """),
        {"operator": operator, "action_id": action_id},
    )
    conn.commit()


def complete_action(
    conn: Connection,
    action_id: str,
    *,
    outcome_notes: str | None = None,
) -> None:
    """Mark an action as completed."""
    conn.execute(
        text("""
        UPDATE actions
        SET status='completed', completed_at=datetime('now'), outcome_notes=:outcome_notes
        WHERE action_id=:action_id
        """),
        {"outcome_notes": outcome_notes, "action_id": action_id},
    )
    conn.commit()


def skip_action(
    conn: Connection,
    action_id: str,
    *,
    outcome_notes: str | None = None,
) -> None:
    """Mark an action as skipped."""
    conn.execute(
        text("""
        UPDATE actions
        SET status='skipped', skipped_at=datetime('now'), outcome_notes=:outcome_notes
        WHERE action_id=:action_id
        """),
        {"outcome_notes": outcome_notes, "action_id": action_id},
    )
    conn.commit()


def get_action(conn: Connection, action_id: str):
    """Fetch action by ID or raise SableError."""
    row = conn.execute(
        text("SELECT * FROM actions WHERE action_id=:action_id"), {"action_id": action_id}
    ).fetchone()
    if not row:
        raise SableError(ENTITY_NOT_FOUND, f"Action '{action_id}' not found")
    return row


def list_actions(
    conn: Connection,
    org_id: str,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list:
    """List actions for an org, optionally filtered by status."""
    if status:
        return conn.execute(
            text("""
            SELECT * FROM actions
            WHERE org_id=:org_id AND status=:status
            ORDER BY created_at DESC LIMIT :limit
            """),
            {"org_id": org_id, "status": status, "limit": limit},
        ).fetchall()
    return conn.execute(
        text("SELECT * FROM actions WHERE org_id=:org_id ORDER BY created_at DESC LIMIT :limit"),
        {"org_id": org_id, "limit": limit},
    ).fetchall()


def action_summary(conn: Connection, org_id: str) -> dict:
    """Return counts by status and execution rate for an org."""
    rows = conn.execute(
        text("SELECT status, COUNT(*) as cnt FROM actions WHERE org_id=:org_id GROUP BY status"),
        {"org_id": org_id},
    ).fetchall()
    counts = {r["status"]: r["cnt"] for r in rows}
    pending = counts.get("pending", 0)
    claimed = counts.get("claimed", 0)
    completed = counts.get("completed", 0)
    skipped = counts.get("skipped", 0)

    denominator = completed + skipped + pending
    execution_rate = (completed / denominator) if denominator > 0 else 0.0

    avg_row = conn.execute(
        text("""
        SELECT AVG(julianday(completed_at) - julianday(created_at)) AS avg_days
        FROM actions
        WHERE org_id=:org_id AND status='completed' AND completed_at IS NOT NULL
        """),
        {"org_id": org_id},
    ).fetchone()
    avg_days = avg_row["avg_days"] if avg_row and avg_row["avg_days"] is not None else None

    return {
        "pending": pending,
        "claimed": claimed,
        "completed": completed,
        "skipped": skipped,
        "total": pending + claimed + completed + skipped,
        "execution_rate": round(execution_rate, 4),
        "avg_days_to_complete": round(avg_days, 1) if avg_days is not None else None,
    }
