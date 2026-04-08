"""Entity interaction edge helpers for sable.db."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.errors import SableError, ORG_NOT_FOUND


def sync_interaction_edges(
    conn: Connection,
    org_id: str,
    edges: list[dict],
    run_date: str,
) -> int:
    """Upsert interaction edges from Cult Grader computed_metrics.

    Each edge dict must have: source_handle, target_handle, interaction_type.
    Optional: count (default 1), first_seen, last_seen.

    Idempotent: updates count + last_seen on conflict, inserts on new edge.
    Returns number of edges upserted.
    """
    row = conn.execute(
        text("SELECT 1 FROM orgs WHERE org_id=:org_id"),
        {"org_id": org_id},
    ).fetchone()
    if not row:
        raise SableError(ORG_NOT_FOUND, f"Org '{org_id}' not found")

    upserted = 0
    for edge in edges:
        source = edge["source_handle"]
        target = edge["target_handle"]
        itype = edge["interaction_type"]
        count = edge.get("count", 1)
        first_seen = edge.get("first_seen")
        last_seen = edge.get("last_seen")

        existing = conn.execute(
            text(
                "SELECT id, count, first_seen FROM entity_interactions"
                " WHERE org_id=:org_id AND source_handle=:source AND target_handle=:target"
                " AND interaction_type=:itype"
            ),
            {"org_id": org_id, "source": source, "target": target, "itype": itype},
        ).fetchone()

        if existing:
            new_count = existing["count"] + count
            new_first = min(existing["first_seen"], first_seen) if existing["first_seen"] and first_seen else (existing["first_seen"] or first_seen)
            conn.execute(
                text(
                    "UPDATE entity_interactions"
                    " SET count=:count, first_seen=:first_seen, last_seen=:last_seen, run_date=:run_date"
                    " WHERE id=:id"
                ),
                {"count": new_count, "first_seen": new_first, "last_seen": last_seen, "run_date": run_date, "id": existing["id"]},
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO entity_interactions"
                    " (org_id, source_handle, target_handle, interaction_type, count,"
                    "  first_seen, last_seen, run_date)"
                    " VALUES (:org_id, :source, :target, :itype, :count,"
                    "  :first_seen, :last_seen, :run_date)"
                ),
                {
                    "org_id": org_id,
                    "source": source,
                    "target": target,
                    "itype": itype,
                    "count": count,
                    "first_seen": first_seen,
                    "last_seen": last_seen,
                    "run_date": run_date,
                },
            )
        upserted += 1

    conn.commit()
    return upserted


def list_interactions(
    conn: Connection,
    org_id: str,
    *,
    interaction_type: str | None = None,
    min_count: int = 1,
    limit: int = 50,
) -> list:
    """List top interaction edges for an org, sorted by count descending."""
    conditions = ["org_id=:org_id", "count >= :min_count"]
    params: dict = {"org_id": org_id, "min_count": min_count}

    if interaction_type:
        conditions.append("interaction_type=:itype")
        params["itype"] = interaction_type

    where = " AND ".join(conditions)
    params["lim"] = limit

    return conn.execute(
        text(f"SELECT * FROM entity_interactions WHERE {where} ORDER BY count DESC LIMIT :lim"),
        params,
    ).fetchall()


def get_interaction_summary(
    conn: Connection,
    org_id: str,
) -> dict:
    """Return aggregate stats for an org's interaction edges."""
    row = conn.execute(
        text(
            "SELECT COUNT(*) as edge_count,"
            " SUM(count) as total_interactions,"
            " COUNT(DISTINCT source_handle) as unique_sources,"
            " COUNT(DISTINCT target_handle) as unique_targets"
            " FROM entity_interactions WHERE org_id=:org_id"
        ),
        {"org_id": org_id},
    ).fetchone()
    return {
        "edge_count": row["edge_count"],
        "total_interactions": row["total_interactions"] or 0,
        "unique_sources": row["unique_sources"],
        "unique_targets": row["unique_targets"],
    }
