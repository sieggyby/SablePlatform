"""Outcome tracking and diagnostic delta helpers for sable.db."""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

TRACKED_METRICS = [
    "fit_score",
    "recurring_account_share",
    "unique_mentioners_count",
    "lateral_reply_pairs",
    "community_graph_density",
    "cultural_production_score",
    "bot_reply_rate",
    "shill_rate",
    "momentum_score",
    "quality_drift",
    "sentiment_positive",
    "sentiment_negative",
    "mvl_stack_score",
    "cultural_term_frequency",
    "content_origination_ratio",
]


def create_outcome(
    conn: sqlite3.Connection,
    org_id: str,
    outcome_type: str,
    *,
    entity_id: str | None = None,
    action_id: str | None = None,
    description: str | None = None,
    metric_name: str | None = None,
    metric_before: float | None = None,
    metric_after: float | None = None,
    data_json: str | None = None,
    recorded_by: str | None = None,
) -> str:
    """Create an outcome record. Returns outcome_id."""
    outcome_id = uuid.uuid4().hex
    metric_delta = None
    if metric_before is not None and metric_after is not None:
        metric_delta = metric_after - metric_before
    conn.execute(
        """
        INSERT INTO outcomes
            (outcome_id, org_id, entity_id, action_id, outcome_type, description,
             metric_name, metric_before, metric_after, metric_delta, data_json, recorded_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (outcome_id, org_id, entity_id, action_id, outcome_type, description,
         metric_name, metric_before, metric_after, metric_delta, data_json, recorded_by),
    )
    conn.commit()
    return outcome_id


def list_outcomes(
    conn: sqlite3.Connection,
    org_id: str,
    *,
    outcome_type: str | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    if outcome_type:
        return conn.execute(
            """
            SELECT * FROM outcomes
            WHERE org_id=? AND outcome_type=?
            ORDER BY created_at DESC LIMIT ?
            """,
            (org_id, outcome_type, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM outcomes WHERE org_id=? ORDER BY created_at DESC LIMIT ?",
        (org_id, limit),
    ).fetchall()


def compute_and_store_diagnostic_delta(
    conn: sqlite3.Connection,
    org_id: str,
    run_id_after: str,
) -> list[str]:
    """Compare run_id_after to the previous completed run. Returns created delta_ids."""
    after_row = conn.execute(
        """
        SELECT run_id, completed_at, checkpoint_path FROM diagnostic_runs
        WHERE org_id=? AND run_id=? AND status='completed'
        """,
        (org_id, run_id_after),
    ).fetchone()
    if not after_row or not after_row["checkpoint_path"]:
        return []

    if after_row["completed_at"] is None:
        prev_row = conn.execute(
            """
            SELECT run_id, checkpoint_path FROM diagnostic_runs
            WHERE org_id=?
              AND run_id < ?
              AND status='completed'
              AND checkpoint_path IS NOT NULL
            ORDER BY run_id DESC LIMIT 1
            """,
            (org_id, run_id_after),
        ).fetchone()
    else:
        prev_row = conn.execute(
            """
            SELECT run_id, checkpoint_path FROM diagnostic_runs
            WHERE org_id=?
              AND run_id != ?
              AND status='completed'
              AND checkpoint_path IS NOT NULL
              AND (
                  (completed_at IS NOT NULL AND (
                      completed_at < ?
                      OR (completed_at = ? AND run_id < ?)
                  ))
                  OR (completed_at IS NULL AND run_id < ?)
              )
            ORDER BY
                CASE WHEN completed_at IS NULL THEN 1 ELSE 0 END,
                completed_at DESC,
                run_id DESC
            LIMIT 1
            """,
            (
                org_id,
                run_id_after,
                after_row["completed_at"],
                after_row["completed_at"],
                run_id_after,
                run_id_after,
            ),
        ).fetchone()
    if not prev_row:
        return []

    before_metrics = _load_metrics(prev_row["checkpoint_path"])
    after_metrics = _load_metrics(after_row["checkpoint_path"])
    if not before_metrics or not after_metrics:
        return []

    delta_ids = []
    for metric in TRACKED_METRICS:
        val_before = before_metrics.get(metric)
        val_after = after_metrics.get(metric)
        if val_before is None and val_after is None:
            continue
        try:
            val_before = float(val_before) if val_before is not None else None
            val_after = float(val_after) if val_after is not None else None
        except (TypeError, ValueError):
            continue

        delta = (val_after - val_before) if (val_before is not None and val_after is not None) else None
        pct = None
        if delta is not None and val_before is not None and val_before != 0:
            pct = delta / val_before

        delta_id = uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO diagnostic_deltas
                (delta_id, org_id, run_id_before, run_id_after, metric_name,
                 value_before, value_after, delta, pct_change)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (delta_id, org_id, prev_row["run_id"], run_id_after, metric,
             val_before, val_after, delta, pct),
        )
        delta_ids.append(delta_id)

    conn.commit()
    return delta_ids


def get_diagnostic_deltas(
    conn: sqlite3.Connection,
    org_id: str,
    *,
    run_id_after: str | None = None,
) -> list[sqlite3.Row]:
    if run_id_after:
        return conn.execute(
            """
            SELECT * FROM diagnostic_deltas
            WHERE org_id=? AND run_id_after=?
            ORDER BY metric_name
            """,
            (org_id, run_id_after),
        ).fetchall()
    return conn.execute(
        """
        SELECT * FROM diagnostic_deltas
        WHERE org_id=?
        ORDER BY created_at DESC, metric_name
        """,
        (org_id,),
    ).fetchall()


def _load_metrics(checkpoint_path: str) -> dict | None:
    p = Path(checkpoint_path) / "computed_metrics.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
