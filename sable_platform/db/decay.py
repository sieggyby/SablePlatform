"""Entity decay score helpers for sable.db.

Decay scores are computed by Cult Grader and synced here via
sync_decay_scores(). Platform stores and alerts on them — it does
not compute them.

Call site: wire into the same sync path as sync_interaction_edges()
once Cult Grader emits a `decay_scores` key in computed_metrics.json.
"""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.errors import SableError, ORG_NOT_FOUND


DECAY_WARNING_THRESHOLD = 0.6
DECAY_CRITICAL_THRESHOLD = 0.8


def sync_decay_scores(
    conn: Connection,
    org_id: str,
    scores: list[dict],
    run_date: str,
) -> int:
    """Upsert decay scores from Cult Grader diagnostic output.

    Each score dict must have: handle, decay_score, risk_tier.
    Optional: factors (dict or None).

    Idempotent: upserts on (org_id, entity_id). The handle is resolved
    to an entity_id via entity_handles when possible; otherwise the raw
    handle is stored.

    Returns number of scores upserted.
    """
    row = conn.execute(text("SELECT 1 FROM orgs WHERE org_id=:org_id"), {"org_id": org_id}).fetchone()
    if not row:
        raise SableError(ORG_NOT_FOUND, f"Org '{org_id}' not found")

    upserted = 0
    for score in scores:
        handle = score["handle"]
        decay_score = score["decay_score"]
        risk_tier = score["risk_tier"]
        factors = score.get("factors")
        factors_json = json.dumps(factors) if factors else None

        # Resolve handle to entity_id when possible
        entity_row = conn.execute(
            text("""
            SELECT e.entity_id FROM entities e
            JOIN entity_handles h ON e.entity_id = h.entity_id
            WHERE e.org_id=:org_id AND h.handle=:handle AND e.status != 'archived'
            """),
            {"org_id": org_id, "handle": handle.lower().lstrip("@")},
        ).fetchone()
        entity_id = entity_row["entity_id"] if entity_row else handle.lower().lstrip("@")

        conn.execute(
            text("""
            INSERT INTO entity_decay_scores
                (org_id, entity_id, decay_score, risk_tier, run_date, factors_json)
            VALUES (:org_id, :entity_id, :decay_score, :risk_tier, :run_date, :factors_json)
            ON CONFLICT (org_id, entity_id) DO UPDATE SET
                decay_score = excluded.decay_score,
                risk_tier = excluded.risk_tier,
                scored_at = datetime('now'),
                run_date = excluded.run_date,
                factors_json = excluded.factors_json
            """),
            {"org_id": org_id, "entity_id": entity_id, "decay_score": decay_score,
             "risk_tier": risk_tier, "run_date": run_date, "factors_json": factors_json},
        )
        upserted += 1

    conn.commit()
    return upserted


def list_decay_scores(
    conn: Connection,
    org_id: str,
    *,
    min_score: float = 0.0,
    risk_tier: str | None = None,
    limit: int = 50,
) -> list:
    """List decay scores for an org, sorted by decay_score descending."""
    conditions = ["org_id=:org_id", "decay_score >= :min_score"]
    params: dict = {"org_id": org_id, "min_score": min_score}

    if risk_tier:
        conditions.append("risk_tier=:risk_tier")
        params["risk_tier"] = risk_tier

    where = " AND ".join(conditions)
    params["limit"] = limit

    return conn.execute(
        text(f"SELECT * FROM entity_decay_scores WHERE {where} ORDER BY decay_score DESC LIMIT :limit"),
        params,
    ).fetchall()


def get_decay_summary(
    conn: Connection,
    org_id: str,
) -> dict:
    """Return aggregate stats for an org's decay scores."""
    row = conn.execute(
        text("""
        SELECT COUNT(*) as scored_entities,
               AVG(decay_score) as avg_score,
               SUM(CASE WHEN risk_tier='critical' THEN 1 ELSE 0 END) as critical_count,
               SUM(CASE WHEN risk_tier='high' THEN 1 ELSE 0 END) as high_count,
               SUM(CASE WHEN risk_tier='medium' THEN 1 ELSE 0 END) as medium_count,
               SUM(CASE WHEN risk_tier='low' THEN 1 ELSE 0 END) as low_count
        FROM entity_decay_scores WHERE org_id=:org_id
        """),
        {"org_id": org_id},
    ).fetchone()
    return {
        "scored_entities": row["scored_entities"],
        "avg_score": round(row["avg_score"], 3) if row["avg_score"] else 0.0,
        "critical_count": row["critical_count"],
        "high_count": row["high_count"],
        "medium_count": row["medium_count"],
        "low_count": row["low_count"],
    }
