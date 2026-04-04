"""Operator audit log helpers for sable.db.

Append-only audit trail for operator and system actions that mutate
org or entity state.
"""
from __future__ import annotations

import json
import sqlite3


def log_audit(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    *,
    org_id: str | None = None,
    entity_id: str | None = None,
    detail: dict | None = None,
    source: str = "cli",
) -> int:
    """Record an audit event. Returns the row id."""
    detail_json = json.dumps(detail) if detail else None
    cursor = conn.execute(
        """
        INSERT INTO audit_log (actor, action, org_id, entity_id, detail_json, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (actor, action, org_id, entity_id, detail_json, source),
    )
    conn.commit()
    return cursor.lastrowid


def list_audit_log(
    conn: sqlite3.Connection,
    *,
    org_id: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    """Query audit log with optional filters. Order by timestamp DESC."""
    conditions: list[str] = []
    params: list = []

    if org_id:
        conditions.append("org_id=?")
        params.append(org_id)
    if actor:
        conditions.append("actor=?")
        params.append(actor)
    if action:
        conditions.append("action=?")
        params.append(action)
    if since:
        conditions.append("timestamp >= ?")
        params.append(since)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)

    return conn.execute(
        f"SELECT * FROM audit_log {where} ORDER BY timestamp DESC LIMIT ?",
        params,
    ).fetchall()
