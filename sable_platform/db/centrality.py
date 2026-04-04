"""Entity centrality score helpers for sable.db.

Centrality scores are computed by Cult Grader and synced here via
sync_centrality_scores(). Platform stores and alerts on them — it does
not compute them.
"""
from __future__ import annotations

import sqlite3

from sable_platform.errors import SableError, ORG_NOT_FOUND


BRIDGE_CENTRALITY_THRESHOLD = 0.3
BRIDGE_DECAY_THRESHOLD = 0.6


def sync_centrality_scores(
    conn: sqlite3.Connection,
    org_id: str,
    scores: list[dict],
    run_date: str,
) -> int:
    """Upsert centrality scores from Cult Grader output.

    Each score dict must have: handle, degree, betweenness, eigenvector.

    Returns number of scores upserted.
    """
    row = conn.execute("SELECT 1 FROM orgs WHERE org_id=?", (org_id,)).fetchone()
    if not row:
        raise SableError(ORG_NOT_FOUND, f"Org '{org_id}' not found")

    upserted = 0
    for score in scores:
        handle = score["handle"]
        degree = score["degree"]
        betweenness = score["betweenness"]
        eigenvector = score["eigenvector"]

        # Resolve handle to entity_id when possible
        entity_row = conn.execute(
            """
            SELECT e.entity_id FROM entities e
            JOIN entity_handles h ON e.entity_id = h.entity_id
            WHERE e.org_id=? AND h.handle=? AND e.status != 'archived'
            """,
            (org_id, handle.lower().lstrip("@")),
        ).fetchone()
        entity_id = entity_row["entity_id"] if entity_row else handle.lower().lstrip("@")

        conn.execute(
            """
            INSERT INTO entity_centrality_scores
                (org_id, entity_id, degree_centrality, betweenness_centrality,
                 eigenvector_centrality, run_date)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (org_id, entity_id) DO UPDATE SET
                degree_centrality = excluded.degree_centrality,
                betweenness_centrality = excluded.betweenness_centrality,
                eigenvector_centrality = excluded.eigenvector_centrality,
                scored_at = datetime('now'),
                run_date = excluded.run_date
            """,
            (org_id, entity_id, degree, betweenness, eigenvector, run_date),
        )
        upserted += 1

    conn.commit()
    return upserted


def list_centrality_scores(
    conn: sqlite3.Connection,
    org_id: str,
    *,
    min_degree: float = 0.0,
    limit: int = 50,
) -> list[sqlite3.Row]:
    """List centrality scores for an org, sorted by betweenness DESC."""
    return conn.execute(
        """
        SELECT * FROM entity_centrality_scores
        WHERE org_id=? AND degree_centrality >= ?
        ORDER BY betweenness_centrality DESC
        LIMIT ?
        """,
        (org_id, min_degree, limit),
    ).fetchall()


def get_centrality_summary(
    conn: sqlite3.Connection,
    org_id: str,
) -> dict:
    """Return aggregate stats for an org's centrality scores."""
    row = conn.execute(
        """
        SELECT COUNT(*) as scored_entities,
               AVG(degree_centrality) as avg_degree,
               AVG(betweenness_centrality) as avg_betweenness
        FROM entity_centrality_scores WHERE org_id=?
        """,
        (org_id,),
    ).fetchone()

    max_row = conn.execute(
        """
        SELECT entity_id FROM entity_centrality_scores
        WHERE org_id=?
        ORDER BY betweenness_centrality DESC LIMIT 1
        """,
        (org_id,),
    ).fetchone()

    return {
        "scored_entities": row["scored_entities"],
        "avg_degree": round(row["avg_degree"], 4) if row["avg_degree"] else 0.0,
        "avg_betweenness": round(row["avg_betweenness"], 4) if row["avg_betweenness"] else 0.0,
        "max_betweenness_entity": max_row["entity_id"] if max_row else None,
    }
