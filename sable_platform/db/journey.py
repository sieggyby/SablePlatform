"""Member journey helpers: tag history, entity timeline, funnel aggregates."""
from __future__ import annotations

import sqlite3

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError as SAOperationalError


def get_entity_journey(conn: Connection, entity_id: str) -> list[dict]:
    """Return a chronological list of events for an entity.

    Each event is a dict with at minimum: {type, timestamp, ...context fields}.
    Sources: entity_tag_history, actions (entity_id match), outcomes (entity_id match).
    """
    events: list[dict] = []

    # Entity creation event
    entity_row = conn.execute(
        text("SELECT org_id, display_name, source, status, created_at FROM entities WHERE entity_id=:entity_id"),
        {"entity_id": entity_id},
    ).fetchone()
    if entity_row:
        events.append({
            "type": "first_seen",
            "timestamp": entity_row["created_at"] or "",
            "source": entity_row["source"],
            "status": entity_row["status"],
            "display_name": entity_row["display_name"],
        })

    # Tag history events
    try:
        tag_rows = conn.execute(
            text(
                "SELECT change_type, tag, confidence, source, expires_at, effective_at"
                " FROM entity_tag_history"
                " WHERE entity_id=:entity_id"
                " ORDER BY effective_at"
            ),
            {"entity_id": entity_id},
        ).fetchall()
        for r in tag_rows:
            events.append({
                "type": "tag_change",
                "timestamp": r["effective_at"] or "",
                "change_type": r["change_type"],
                "tag": r["tag"],
                "confidence": r["confidence"],
                "source": r["source"],
                "expires_at": r["expires_at"],
            })
    except (sqlite3.OperationalError, SAOperationalError):
        pass  # table absent before migration 008

    # Action events
    try:
        action_rows = conn.execute(
            text(
                "SELECT action_type, title, status, operator, outcome_notes,"
                "       created_at, claimed_at, completed_at, skipped_at"
                " FROM actions"
                " WHERE entity_id=:entity_id"
                " ORDER BY created_at"
            ),
            {"entity_id": entity_id},
        ).fetchall()
        for r in action_rows:
            events.append({
                "type": "action",
                "timestamp": r["created_at"] or "",
                "action_type": r["action_type"],
                "title": r["title"],
                "status": r["status"],
                "operator": r["operator"],
                "outcome_notes": r["outcome_notes"],
            })
            if r["claimed_at"]:
                events.append({
                    "type": "action_claimed",
                    "timestamp": r["claimed_at"],
                    "title": r["title"],
                    "operator": r["operator"],
                })
            if r["completed_at"]:
                events.append({
                    "type": "action_completed",
                    "timestamp": r["completed_at"],
                    "title": r["title"],
                    "notes": r["outcome_notes"],
                })
            elif r["skipped_at"]:
                events.append({
                    "type": "action_skipped",
                    "timestamp": r["skipped_at"],
                    "title": r["title"],
                })
    except (sqlite3.OperationalError, SAOperationalError):
        pass  # table absent before migration 007

    # Outcome events
    try:
        outcome_rows = conn.execute(
            text(
                "SELECT outcome_type, description, metric_name, metric_before, metric_after, created_at"
                " FROM outcomes"
                " WHERE entity_id=:entity_id"
                " ORDER BY created_at"
            ),
            {"entity_id": entity_id},
        ).fetchall()
        for r in outcome_rows:
            events.append({
                "type": "outcome",
                "timestamp": r["created_at"] or "",
                "outcome_type": r["outcome_type"],
                "description": r["description"],
                "metric_name": r["metric_name"],
                "metric_before": r["metric_before"],
                "metric_after": r["metric_after"],
            })
    except (sqlite3.OperationalError, SAOperationalError):
        pass  # table absent before migration 007

    events.sort(key=lambda e: e.get("timestamp") or "")
    return events


def entity_funnel(conn: Connection, org_id: str) -> dict:
    """Return aggregate funnel counts for an org."""
    total = conn.execute(
        text("SELECT COUNT(*) FROM entities WHERE org_id=:org_id AND status != 'archived'"),
        {"org_id": org_id},
    ).fetchone()[0]

    cultist_count = conn.execute(
        text(
            "SELECT COUNT(DISTINCT e.entity_id)"
            " FROM entities e"
            " JOIN entity_tags t ON e.entity_id = t.entity_id"
            " WHERE e.org_id=:org_id AND t.tag='cultist_candidate'"
            "   AND t.is_current=1 AND (t.expires_at IS NULL OR t.expires_at > datetime('now'))"
        ),
        {"org_id": org_id},
    ).fetchone()[0]

    top_contrib_count = conn.execute(
        text(
            "SELECT COUNT(DISTINCT e.entity_id)"
            " FROM entities e"
            " JOIN entity_tags t ON e.entity_id = t.entity_id"
            " WHERE e.org_id=:org_id AND t.tag='top_contributor'"
            "   AND t.is_current=1 AND (t.expires_at IS NULL OR t.expires_at > datetime('now'))"
        ),
        {"org_id": org_id},
    ).fetchone()[0]

    # Average days from entity creation to first cultist_candidate tag
    avg_cultist = None
    try:
        row = conn.execute(
            text(
                "SELECT AVG(julianday(h.effective_at) - julianday(e.created_at)) AS avg_days"
                " FROM entity_tag_history h"
                " JOIN entities e ON h.entity_id = e.entity_id"
                " WHERE e.org_id=:org_id AND h.tag='cultist_candidate' AND h.change_type='added'"
            ),
            {"org_id": org_id},
        ).fetchone()
        avg_cultist = round(row["avg_days"], 1) if row and row["avg_days"] is not None else None
    except (sqlite3.OperationalError, SAOperationalError):
        pass  # entity_tag_history absent before migration 008

    avg_top_contrib = None
    try:
        row = conn.execute(
            text(
                "SELECT AVG(julianday(h.effective_at) - julianday(e.created_at)) AS avg_days"
                " FROM entity_tag_history h"
                " JOIN entities e ON h.entity_id = e.entity_id"
                " WHERE e.org_id=:org_id AND h.tag='top_contributor' AND h.change_type='added'"
            ),
            {"org_id": org_id},
        ).fetchone()
        avg_top_contrib = round(row["avg_days"], 1) if row and row["avg_days"] is not None else None
    except (sqlite3.OperationalError, SAOperationalError):
        pass  # entity_tag_history absent before migration 008

    return {
        "total_entities": total,
        "cultist_candidate_count": cultist_count,
        "top_contributor_count": top_contrib_count,
        "avg_days_to_cultist": avg_cultist,
        "avg_days_to_top_contributor": avg_top_contrib,
    }


def get_key_journeys(
    conn: Connection,
    org_id: str,
    limit: int = 5,
) -> list[dict]:
    """Return the N most event-rich entity journeys for an org.

    Scores entities by total event count (tag history + actions + outcomes),
    then returns the top N with their full journey via get_entity_journey().
    """
    entity_rows = conn.execute(
        text("SELECT entity_id, display_name FROM entities WHERE org_id=:org_id AND status != 'archived'"),
        {"org_id": org_id},
    ).fetchall()

    scored: list[tuple[int, str, str]] = []
    for r in entity_rows:
        eid = r["entity_id"]
        count = 0
        try:
            count += conn.execute(
                text("SELECT COUNT(*) FROM entity_tag_history WHERE entity_id=:entity_id"),
                {"entity_id": eid},
            ).fetchone()[0]
        except (sqlite3.OperationalError, SAOperationalError):
            pass
        try:
            count += conn.execute(
                text("SELECT COUNT(*) FROM actions WHERE entity_id=:entity_id"),
                {"entity_id": eid},
            ).fetchone()[0]
        except (sqlite3.OperationalError, SAOperationalError):
            pass
        try:
            count += conn.execute(
                text("SELECT COUNT(*) FROM outcomes WHERE entity_id=:entity_id"),
                {"entity_id": eid},
            ).fetchone()[0]
        except (sqlite3.OperationalError, SAOperationalError):
            pass
        scored.append((count, eid, r["display_name"] or ""))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:limit]

    results = []
    for event_count, eid, display_name in top:
        events = get_entity_journey(conn, eid)
        results.append({
            "entity_id": eid,
            "display_name": display_name,
            "event_count": event_count,
            "events": events,
        })
    return results


def first_seen_list(
    conn: Connection,
    org_id: str,
    *,
    source: str | None = None,
    limit: int = 50,
) -> list:
    """List entities for an org ordered by first seen (created_at)."""
    if source:
        return conn.execute(
            text(
                "SELECT entity_id, display_name, source, status, created_at"
                " FROM entities"
                " WHERE org_id=:org_id AND source=:source AND status != 'archived'"
                " ORDER BY created_at DESC LIMIT :lim"
            ),
            {"org_id": org_id, "source": source, "lim": limit},
        ).fetchall()
    return conn.execute(
        text(
            "SELECT entity_id, display_name, source, status, created_at"
            " FROM entities"
            " WHERE org_id=:org_id AND status != 'archived'"
            " ORDER BY created_at DESC LIMIT :lim"
        ),
        {"org_id": org_id, "lim": limit},
    ).fetchall()
