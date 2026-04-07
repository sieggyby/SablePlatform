"""Prospect pipeline query — joins prospect_scores with diagnostic_runs."""
from __future__ import annotations

import sqlite3


def query_prospect_pipeline(
    conn: sqlite3.Connection,
    *,
    tier: str | None = None,
    stale_days: int | None = None,
    limit: int = 50,
) -> list[dict]:
    """Query prospect_scores joined with latest diagnostic_runs per org_id.

    NOTE: prospect_scores.org_id is semantically a project_id (the prospect
    being evaluated), NOT a Sable client org_id. The JOIN to diagnostic_runs
    matches on org_id from both tables.

    Returns dicts with: org_id, composite_score, tier, fit_score,
    diagnostic_date, days_since_last_diagnostic, recommended_action.
    """
    # Subquery: latest completed diagnostic per org_id
    query = """
        SELECT
            ps.org_id,
            ps.composite_score,
            ps.tier,
            ps.run_date,
            ps.recommended_action AS prospect_action,
            d.fit_score,
            d.recommended_action AS diag_action,
            d.completed_at AS diagnostic_date,
            CASE
                WHEN d.completed_at IS NOT NULL
                THEN CAST(julianday('now') - julianday(d.completed_at) AS INTEGER)
                ELSE NULL
            END AS days_since_last_diagnostic
        FROM prospect_scores ps
        LEFT JOIN (
            SELECT org_id, fit_score, recommended_action, completed_at,
                   ROW_NUMBER() OVER (PARTITION BY org_id ORDER BY completed_at DESC) AS rn
            FROM diagnostic_runs
            WHERE status = 'completed'
        ) d ON ps.org_id = d.org_id AND d.rn = 1
        WHERE ps.run_date = (SELECT MAX(run_date) FROM prospect_scores)
          AND ps.graduated_at IS NULL
          AND ps.rejected_at IS NULL
    """
    params: list = []

    if tier:
        query += " AND ps.tier = ?"
        params.append(tier)

    if stale_days is not None:
        query += """
            AND (d.completed_at IS NULL
                 OR CAST(julianday('now') - julianday(d.completed_at) AS INTEGER) > ?)
        """
        params.append(stale_days)

    query += " ORDER BY ps.composite_score DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    results = []
    for r in rows:
        results.append({
            "org_id": r["org_id"],
            "composite_score": r["composite_score"],
            "tier": r["tier"],
            "fit_score": r["fit_score"],
            "diagnostic_date": r["diagnostic_date"],
            "days_since_last_diagnostic": r["days_since_last_diagnostic"],
            "recommended_action": r["diag_action"] or r["prospect_action"] or "",
        })

    return results
