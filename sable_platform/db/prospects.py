"""Prospect scoring helpers for sable.db.

Prospect scores are computed by Sable_Community_Lead_Identifier and synced
here via sync_prospect_scores(). Platform stores and queries them — it does
not compute them.
"""
from __future__ import annotations

import json
import sqlite3


def sync_prospect_scores(
    conn: sqlite3.Connection,
    scores: list[dict],
    run_date: str,
) -> int:
    """Upsert prospect scores from Lead Identifier output.

    Each score dict must have: org_id, composite_score, tier.
    Optional: stage, dimensions (dict), rationale (dict), enrichment (dict),
    next_action (str).

    Idempotent: upserts on (org_id, run_date).

    Returns number of scores upserted.
    """
    upserted = 0
    for score in scores:
        org_id = score["org_id"]
        composite = score["composite_score"]
        tier = score["tier"]
        stage = score.get("stage")
        dimensions = score.get("dimensions")
        rationale = score.get("rationale")
        enrichment = score.get("enrichment")
        next_action = score.get("next_action")

        conn.execute(
            """
            INSERT INTO prospect_scores
                (org_id, run_date, composite_score, tier, stage,
                 dimensions_json, rationale_json, enrichment_json, next_action)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (org_id, run_date) DO UPDATE SET
                composite_score = excluded.composite_score,
                tier = excluded.tier,
                stage = excluded.stage,
                dimensions_json = excluded.dimensions_json,
                rationale_json = excluded.rationale_json,
                enrichment_json = excluded.enrichment_json,
                next_action = excluded.next_action,
                scored_at = datetime('now')
            """,
            (
                org_id, run_date, composite, tier, stage,
                json.dumps(dimensions or {}),
                json.dumps(rationale) if rationale else None,
                json.dumps(enrichment) if enrichment else None,
                next_action,
            ),
        )
        upserted += 1

    conn.commit()
    return upserted


def list_prospect_scores(
    conn: sqlite3.Connection,
    *,
    min_score: float = 0.0,
    tier: str | None = None,
    run_date: str | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    """List prospect scores, defaulting to latest run_date.

    Sorted by composite_score descending.
    """
    conditions = ["composite_score >= ?"]
    params: list = [min_score]

    if tier:
        conditions.append("tier=?")
        params.append(tier)

    if run_date:
        conditions.append("run_date=?")
        params.append(run_date)
    else:
        # Default to latest run_date
        conditions.append("run_date = (SELECT MAX(run_date) FROM prospect_scores)")

    where = " AND ".join(conditions)
    params.append(limit)

    return conn.execute(
        f"SELECT * FROM prospect_scores WHERE {where} ORDER BY composite_score DESC LIMIT ?",
        params,
    ).fetchall()


def get_prospect_summary(
    conn: sqlite3.Connection,
    run_date: str | None = None,
) -> dict:
    """Return aggregate prospect score summary for a run date."""
    if not run_date:
        row = conn.execute("SELECT MAX(run_date) as rd FROM prospect_scores").fetchone()
        run_date = row["rd"] if row else None

    if not run_date:
        return {"total_scored": 0, "by_tier": {}, "run_date": None}

    rows = conn.execute(
        "SELECT tier, COUNT(*) as cnt FROM prospect_scores WHERE run_date=? GROUP BY tier",
        (run_date,),
    ).fetchall()

    by_tier = {r["tier"]: r["cnt"] for r in rows}
    total = sum(by_tier.values())

    return {
        "total_scored": total,
        "by_tier": by_tier,
        "run_date": run_date,
    }
