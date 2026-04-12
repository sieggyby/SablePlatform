"""Operator audit log helpers for sable.db.

Append-only audit trail for operator and system actions that mutate
org or entity state.
"""
from __future__ import annotations

import json
import os

from sqlalchemy import text
from sqlalchemy.engine import Connection


def _resolve_actor(actor: str) -> str:
    """Use SABLE_OPERATOR_ID when caller passes 'unknown' (the unresolved default)."""
    if actor == "unknown":
        return os.environ.get("SABLE_OPERATOR_ID", actor)
    return actor


def log_audit(
    conn: Connection,
    actor: str,
    action: str,
    *,
    org_id: str | None = None,
    entity_id: str | None = None,
    detail: dict | None = None,
    source: str = "cli",
) -> int:
    """Record an audit event. Returns the row id."""
    resolved_actor = _resolve_actor(actor)
    detail_json = json.dumps(detail) if detail else None
    row = conn.execute(
        text(
            "INSERT INTO audit_log (actor, action, org_id, entity_id, detail_json, source)"
            " VALUES (:actor, :action, :org_id, :entity_id, :detail_json, :source)"
            " RETURNING id"
        ),
        {
            "actor": resolved_actor,
            "action": action,
            "org_id": org_id,
            "entity_id": entity_id,
            "detail_json": detail_json,
            "source": source,
        },
    ).fetchone()
    conn.commit()
    if row is None:
        raise RuntimeError("INSERT INTO audit_log did not return id")
    return row[0]


def list_audit_log(
    conn: Connection,
    *,
    org_id: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> list:
    """Query audit log with optional filters. Order by timestamp DESC."""
    conditions: list[str] = []
    params: dict = {}

    if org_id:
        conditions.append("org_id=:org_id")
        params["org_id"] = org_id
    if actor:
        conditions.append("actor=:actor")
        params["actor"] = actor
    if action:
        conditions.append("action=:action")
        params["action"] = action
    if since:
        conditions.append("timestamp >= :since")
        params["since"] = since

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params["lim"] = limit

    return conn.execute(
        text(f"SELECT * FROM audit_log {where} ORDER BY timestamp DESC LIMIT :lim"),
        params,
    ).fetchall()
