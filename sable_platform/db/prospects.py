"""Prospect scoring helpers for sable.db.

Prospect scores are computed by Sable_Community_Lead_Identifier and synced
here via sync_prospect_scores(). Platform stores and queries them — it does
not compute them.
"""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Connection


def sync_prospect_scores(
    conn: Connection,
    scores: list[dict],
    run_date: str,
) -> int:
    """Upsert prospect scores from Lead Identifier output.

    Each score dict must have: org_id, composite_score, tier.
    Optional: stage, dimensions (dict), rationale (dict), enrichment (dict),
    next_action (str).

    Graduation/rejection lifecycle markers are carried forward from prior rows
    for the same org_id so rescoring does not resurrect hidden prospects.

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
        recommended_action = score.get("recommended_action")
        score_band_low = score.get("score_band_low")
        score_band_high = score.get("score_band_high")
        timing_urgency = score.get("timing_urgency")
        prev_flags = conn.execute(
            text(
                "SELECT MAX(graduated_at) AS graduated_at, MAX(rejected_at) AS rejected_at"
                " FROM prospect_scores"
                " WHERE org_id = :org_id"
            ),
            {"org_id": org_id},
        ).fetchone()
        graduated_at = score.get("graduated_at") or (prev_flags["graduated_at"] if prev_flags else None)
        rejected_at = score.get("rejected_at") or (prev_flags["rejected_at"] if prev_flags else None)

        conn.execute(
            text(
                "INSERT INTO prospect_scores"
                " (org_id, run_date, composite_score, tier, stage,"
                "  dimensions_json, rationale_json, enrichment_json, next_action,"
                "  recommended_action, score_band_low, score_band_high, timing_urgency,"
                "  graduated_at, rejected_at)"
                " VALUES (:org_id, :run_date, :composite_score, :tier, :stage,"
                "  :dimensions_json, :rationale_json, :enrichment_json, :next_action,"
                "  :recommended_action, :score_band_low, :score_band_high, :timing_urgency,"
                "  :graduated_at, :rejected_at)"
                " ON CONFLICT (org_id, run_date) DO UPDATE SET"
                "  composite_score = excluded.composite_score,"
                "  tier = excluded.tier,"
                "  stage = excluded.stage,"
                "  dimensions_json = excluded.dimensions_json,"
                "  rationale_json = excluded.rationale_json,"
                "  enrichment_json = excluded.enrichment_json,"
                "  next_action = excluded.next_action,"
                "  recommended_action = excluded.recommended_action,"
                "  score_band_low = excluded.score_band_low,"
                "  score_band_high = excluded.score_band_high,"
                "  timing_urgency = excluded.timing_urgency,"
                "  graduated_at = COALESCE(prospect_scores.graduated_at, excluded.graduated_at),"
                "  rejected_at = COALESCE(prospect_scores.rejected_at, excluded.rejected_at),"
                "  scored_at = CURRENT_TIMESTAMP"
            ),
            {
                "org_id": org_id,
                "run_date": run_date,
                "composite_score": composite,
                "tier": tier,
                "stage": stage,
                "dimensions_json": json.dumps(dimensions or {}),
                "rationale_json": json.dumps(rationale) if rationale else None,
                "enrichment_json": json.dumps(enrichment) if enrichment else None,
                "next_action": next_action,
                "recommended_action": recommended_action,
                "score_band_low": score_band_low,
                "score_band_high": score_band_high,
                "timing_urgency": timing_urgency,
                "graduated_at": graduated_at,
                "rejected_at": rejected_at,
            },
        )
        upserted += 1

    conn.commit()
    return upserted


def list_prospect_scores(
    conn: Connection,
    *,
    min_score: float = 0.0,
    tier: str | None = None,
    run_date: str | None = None,
    include_graduated: bool = False,
    include_rejected: bool = False,
    limit: int = 50,
) -> list:
    """List prospect scores, defaulting to latest run_date.

    By default excludes graduated and rejected prospects.
    Pass include_graduated=True / include_rejected=True to include them.
    Sorted by composite_score descending.
    """
    conditions = ["composite_score >= :min_score"]
    params: dict = {"min_score": min_score}

    if not include_graduated:
        conditions.append("graduated_at IS NULL")

    if not include_rejected:
        conditions.append("rejected_at IS NULL")

    if tier:
        conditions.append("tier=:tier")
        params["tier"] = tier

    if run_date:
        conditions.append("run_date=:run_date")
        params["run_date"] = run_date
    else:
        # Default to latest run_date
        conditions.append("run_date = (SELECT MAX(run_date) FROM prospect_scores)")

    where = " AND ".join(conditions)
    params["lim"] = limit

    return conn.execute(
        text(f"SELECT * FROM prospect_scores WHERE {where} ORDER BY composite_score DESC LIMIT :lim"),
        params,
    ).fetchall()


def graduate_prospect(conn: Connection, project_id: str) -> int:
    """Mark all prospect_scores rows for a project as graduated.

    Stamps graduated_at on matching rows where graduated_at IS NULL.
    Returns the number of rows updated.
    """
    cursor = conn.execute(
        text("UPDATE prospect_scores SET graduated_at = CURRENT_TIMESTAMP WHERE org_id = :org_id AND graduated_at IS NULL"),
        {"org_id": project_id},
    )
    conn.commit()
    return cursor.rowcount


def reject_prospect(conn: Connection, project_id: str) -> int:
    """Mark all prospect_scores rows for a project as rejected.

    Stamps rejected_at on matching rows where rejected_at IS NULL.
    Returns the number of rows updated.
    """
    cursor = conn.execute(
        text("UPDATE prospect_scores SET rejected_at = CURRENT_TIMESTAMP WHERE org_id = :org_id AND rejected_at IS NULL"),
        {"org_id": project_id},
    )
    conn.commit()
    return cursor.rowcount


def get_prospect_summary(
    conn: Connection,
    run_date: str | None = None,
) -> dict:
    """Return aggregate prospect score summary for a run date."""
    if not run_date:
        row = conn.execute(text("SELECT MAX(run_date) as rd FROM prospect_scores")).fetchone()
        run_date = row["rd"] if row else None

    if not run_date:
        return {"total_scored": 0, "by_tier": {}, "run_date": None}

    rows = conn.execute(
        text("SELECT tier, COUNT(*) as cnt FROM prospect_scores WHERE run_date=:run_date GROUP BY tier"),
        {"run_date": run_date},
    ).fetchall()

    by_tier = {r["tier"]: r["cnt"] for r in rows}
    total = sum(by_tier.values())

    return {
        "total_scored": total,
        "by_tier": by_tier,
        "run_date": run_date,
    }
