"""Data retention garbage collection for sable.db.

Purges old workflow events, terminal workflow runs/steps, cost events,
and resolved alerts. NEVER purges audit_log.
"""
from __future__ import annotations

import sqlite3


def run_gc(conn: sqlite3.Connection, retention_days: int = 90) -> dict:
    """Purge records older than retention_days. Returns counts of deleted rows.

    Safe to run on an empty DB (returns all zeros).
    """
    threshold = f"-{retention_days} days"
    counts: dict[str, int] = {}

    # Identify terminal runs to purge (need IDs for FK-safe deletion order)
    old_run_ids = [
        r[0] for r in conn.execute(
            """
            SELECT run_id FROM workflow_runs
            WHERE status IN ('completed', 'failed', 'cancelled')
              AND completed_at < datetime('now', ?)
            """,
            (threshold,),
        ).fetchall()
    ]

    # Delete in FK-safe order: events → steps → runs
    events_deleted = 0
    steps_deleted = 0
    if old_run_ids:
        placeholders = ",".join("?" * len(old_run_ids))
        cur = conn.execute(
            f"DELETE FROM workflow_events WHERE run_id IN ({placeholders})",
            old_run_ids,
        )
        events_deleted = cur.rowcount

        cur = conn.execute(
            f"DELETE FROM workflow_steps WHERE run_id IN ({placeholders})",
            old_run_ids,
        )
        steps_deleted = cur.rowcount

        cur = conn.execute(
            f"DELETE FROM workflow_runs WHERE run_id IN ({placeholders})",
            old_run_ids,
        )
        counts["workflow_runs"] = cur.rowcount
    else:
        counts["workflow_runs"] = 0

    # Also purge orphan events older than threshold (events for non-terminal runs)
    cur = conn.execute(
        "DELETE FROM workflow_events WHERE created_at < datetime('now', ?) AND run_id NOT IN (SELECT run_id FROM workflow_runs)",
        (threshold,),
    )
    counts["workflow_events"] = events_deleted + cur.rowcount
    counts["workflow_steps"] = steps_deleted

    # Cost events — all rows older than threshold are deleted (no rollup implemented)
    cur = conn.execute(
        "DELETE FROM cost_events WHERE created_at < datetime('now', ?)",
        (threshold,),
    )
    counts["cost_events"] = cur.rowcount

    # Resolved alerts
    cur = conn.execute(
        """
        DELETE FROM alerts
        WHERE status = 'resolved'
          AND created_at < datetime('now', ?)
        """,
        (threshold,),
    )
    counts["alerts"] = cur.rowcount

    conn.commit()

    # audit_log is NEVER purged
    counts["audit_log"] = 0

    return counts
