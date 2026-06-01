"""Operator work-tracking helpers (migration 059, SW-TASKING Phase 1).

Two surfaces:
  * mod-slot "clock-in" sessions (operator-*declared* coverage windows)
  * a generic operator work-event log

plus ``get_work_summary`` — the ops "scale of work delivered" rollup. Replies
are NOT stored here: ``get_work_summary`` counts them from ``reply_outcomes``
(mig 056) via ``replies.count_replies_delivered`` so there is one source of
truth for replies.

Timestamp contract (post-053): every write BINDS an explicit ``_iso_z`` string
(``...T...Z``) rather than relying on the column default, so SQLite and Postgres
stay lexically comparable for the windowed reads. CompatConnection ``Row``
objects yield column *names* when iterated/unpacked, so every read indexes
positionally (``r[0]``), never by key.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection


def _iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _duration_seconds(start: str, end: str) -> float:
    try:
        return max(0.0, (_parse(end) - _parse(start)).total_seconds())
    except (ValueError, TypeError):
        return 0.0


# ---- mod-slot sessions ----------------------------------------------------

def open_mod_slot(
    conn: Connection,
    org_id: str,
    operator_handle: str,
    chats_watched: Iterable[str] | None = None,
    note: str | None = None,
) -> str:
    """Open a moderating slot for an operator. Closes any slot the operator
    already has open first (an operator can only watch one slot at a time)."""
    close_mod_slot(conn, operator_handle)
    session_id = uuid.uuid4().hex
    now = _iso_z()
    conn.execute(
        text(
            "INSERT INTO mod_slot_sessions"
            " (session_id, org_id, operator_handle, started_at, ended_at,"
            "  chats_watched_json, note, created_at)"
            " VALUES (:sid, :org, :op, :started, NULL, :chats, :note, :created)"
        ),
        {
            "sid": session_id,
            "org": org_id,
            "op": operator_handle,
            "started": now,
            "chats": json.dumps(list(chats_watched or [])),
            "note": note,
            "created": now,
        },
    )
    return session_id


def close_mod_slot(conn: Connection, operator_handle: str, ended_at: str | None = None) -> bool:
    """Close the operator's currently-open slot. Returns True if a slot was
    closed, False if there was none open."""
    res = conn.execute(
        text(
            "UPDATE mod_slot_sessions SET ended_at = :ended"
            " WHERE operator_handle = :op AND ended_at IS NULL"
        ),
        {"ended": ended_at or _iso_z(), "op": operator_handle},
    )
    return (res.rowcount or 0) > 0


def list_active_slots(conn: Connection, org_id: str | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT session_id, org_id, operator_handle, started_at, chats_watched_json, note"
        " FROM mod_slot_sessions WHERE ended_at IS NULL"
    )
    params: dict[str, Any] = {}
    if org_id is not None:
        sql += " AND org_id = :org"
        params["org"] = org_id
    sql += " ORDER BY started_at DESC"
    rows = conn.execute(text(sql), params).fetchall()
    return [
        {
            "session_id": r[0],
            "org_id": r[1],
            "operator_handle": r[2],
            "started_at": r[3],
            "chats_watched": json.loads(r[4] or "[]"),
            "note": r[5],
        }
        for r in rows
    ]


def list_sessions(
    conn: Connection, org_id: str, since: str | None = None, until: str | None = None
) -> list[dict[str, Any]]:
    sql = (
        "SELECT session_id, operator_handle, started_at, ended_at, chats_watched_json, note"
        " FROM mod_slot_sessions WHERE org_id = :org"
    )
    params: dict[str, Any] = {"org": org_id}
    if since is not None:
        sql += " AND started_at >= :since"
        params["since"] = since
    if until is not None:
        sql += " AND started_at < :until"
        params["until"] = until
    sql += " ORDER BY started_at DESC"
    rows = conn.execute(text(sql), params).fetchall()
    return [
        {
            "session_id": r[0],
            "operator_handle": r[1],
            "started_at": r[2],
            "ended_at": r[3],
            "chats_watched": json.loads(r[4] or "[]"),
            "note": r[5],
        }
        for r in rows
    ]


# ---- generic work events --------------------------------------------------

def log_work_event(
    conn: Connection,
    org_id: str,
    operator_handle: str,
    event_type: str,
    ref: Any = None,
) -> str:
    event_id = uuid.uuid4().hex
    now = _iso_z()
    conn.execute(
        text(
            "INSERT INTO operator_work_events"
            " (event_id, org_id, operator_handle, event_type, occurred_at, ref_json, created_at)"
            " VALUES (:eid, :org, :op, :etype, :occurred, :ref, :created)"
        ),
        {
            "eid": event_id,
            "org": org_id,
            "op": operator_handle,
            "etype": event_type,
            "occurred": now,
            "ref": json.dumps(ref) if ref is not None else None,
            "created": now,
        },
    )
    return event_id


# ---- the rollup -----------------------------------------------------------

def get_work_summary(
    conn: Connection, org_id: str, since: str | None = None, until: str | None = None
) -> dict[str, Any]:
    """The ops 'scale of work delivered' rollup for one org over a window.

    ``replies_delivered`` is *measured* (posted reply outcomes). Coverage hours
    and communities are *self-reported* (derived from operator-declared slots);
    only CLOSED sessions contribute hours. ``per_operator`` is OPS-ONLY and must
    be stripped before any client surface.
    """
    # Imported lazily to avoid a circular import at module load.
    from sable_platform.db.replies import count_replies_delivered

    sessions = list_sessions(conn, org_id, since, until)
    coverage_seconds = 0.0
    communities: set[str] = set()
    per_op: dict[str, dict[str, Any]] = {}
    for s in sessions:
        op = s["operator_handle"]
        rec = per_op.setdefault(
            op,
            {"operator_handle": op, "sessions": 0, "coverage_hours": 0.0, "_communities": set()},
        )
        rec["sessions"] += 1
        for chat in s["chats_watched"]:
            communities.add(chat)
            rec["_communities"].add(chat)
        if s["ended_at"]:  # only closed sessions count toward hours
            dur = _duration_seconds(s["started_at"], s["ended_at"])
            coverage_seconds += dur
            rec["coverage_hours"] += dur / 3600.0

    per_operator = [
        {
            "operator_handle": v["operator_handle"],
            "sessions": v["sessions"],
            "coverage_hours": round(v["coverage_hours"], 2),
            "communities_covered": len(v["_communities"]),
        }
        for v in per_op.values()
    ]

    return {
        "replies_delivered": count_replies_delivered(conn, org_id, since, until),
        "declared_coverage_hours": round(coverage_seconds / 3600.0, 2),
        "communities_covered": len(communities),
        "active_now": len(list_active_slots(conn, org_id)),
        "per_operator": per_operator,
        "window": {"since": since, "until": until},
        "generated_at": _iso_z(),
    }
