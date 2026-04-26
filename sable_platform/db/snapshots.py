"""Metric snapshot CRUD for client_checkin_loop week-over-week persistence.

Each Friday the check-in workflow writes one row per org with the
serialized tier-1 + tier-2 metrics it surfaced. The following Friday
reads the most recent prior snapshot to compute deltas.

Platform stores and queries — it does not interpret. Callers own the
shape of ``metrics_json``; this module is type-agnostic.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


def upsert_metric_snapshot(
    conn: Connection,
    org_id: str,
    snapshot_date: str,
    metrics: dict[str, Any],
    source: str,
) -> int:
    """Insert or update the snapshot for (org_id, snapshot_date).

    Returns the row id. ``source`` is one of 'cult_grader', 'pipeline',
    'manual'. ``snapshot_date`` is an ISO date string (YYYY-MM-DD).
    """
    metrics_json = json.dumps(metrics, sort_keys=True)
    existing = conn.execute(
        text(
            "SELECT id FROM metric_snapshots"
            " WHERE org_id = :org_id AND snapshot_date = :snapshot_date"
        ),
        {"org_id": org_id, "snapshot_date": snapshot_date},
    ).fetchone()

    if existing:
        conn.execute(
            text(
                "UPDATE metric_snapshots"
                " SET metrics_json = :metrics_json, source = :source"
                " WHERE id = :id"
            ),
            {"metrics_json": metrics_json, "source": source, "id": existing[0]},
        )
        return int(existing[0])

    row = conn.execute(
        text(
            "INSERT INTO metric_snapshots (org_id, snapshot_date, metrics_json, source)"
            " VALUES (:org_id, :snapshot_date, :metrics_json, :source)"
            " RETURNING id"
        ),
        {
            "org_id": org_id,
            "snapshot_date": snapshot_date,
            "metrics_json": metrics_json,
            "source": source,
        },
    ).fetchone()
    return int(row[0])


def get_snapshot(
    conn: Connection,
    org_id: str,
    snapshot_date: str,
) -> dict | None:
    """Return the snapshot for an exact (org_id, snapshot_date), or None."""
    row = conn.execute(
        text(
            "SELECT id, org_id, snapshot_date, metrics_json, source, created_at"
            " FROM metric_snapshots"
            " WHERE org_id = :org_id AND snapshot_date = :snapshot_date"
        ),
        {"org_id": org_id, "snapshot_date": snapshot_date},
    ).fetchone()
    if not row:
        return None
    return _hydrate(row)


def get_latest_snapshot(
    conn: Connection,
    org_id: str,
    before_date: str | None = None,
) -> dict | None:
    """Return the most recent snapshot for an org, optionally strictly before a date.

    Used by the check-in workflow to find last week's baseline for WoW deltas.
    Pass ``before_date=this_friday`` to exclude today's snapshot if it has
    already been written.
    """
    if before_date:
        row = conn.execute(
            text(
                "SELECT id, org_id, snapshot_date, metrics_json, source, created_at"
                " FROM metric_snapshots"
                " WHERE org_id = :org_id AND snapshot_date < :before_date"
                " ORDER BY snapshot_date DESC LIMIT 1"
            ),
            {"org_id": org_id, "before_date": before_date},
        ).fetchone()
    else:
        row = conn.execute(
            text(
                "SELECT id, org_id, snapshot_date, metrics_json, source, created_at"
                " FROM metric_snapshots"
                " WHERE org_id = :org_id"
                " ORDER BY snapshot_date DESC LIMIT 1"
            ),
            {"org_id": org_id},
        ).fetchone()
    if not row:
        return None
    return _hydrate(row)


def list_snapshots(
    conn: Connection,
    org_id: str,
    limit: int = 10,
) -> list[dict]:
    """Return up to ``limit`` recent snapshots for an org, newest first."""
    rows = conn.execute(
        text(
            "SELECT id, org_id, snapshot_date, metrics_json, source, created_at"
            " FROM metric_snapshots"
            " WHERE org_id = :org_id"
            " ORDER BY snapshot_date DESC LIMIT :limit"
        ),
        {"org_id": org_id, "limit": limit},
    ).fetchall()
    return [_hydrate(r) for r in rows]


def _hydrate(row) -> dict:
    return {
        "id": int(row[0]),
        "org_id": row[1],
        "snapshot_date": row[2],
        "metrics": json.loads(row[3]) if row[3] else {},
        "source": row[4],
        "created_at": row[5],
    }
