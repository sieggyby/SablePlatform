"""Programmatic health check for sable.db."""
from __future__ import annotations

import sqlite3


def check_db_health(conn: sqlite3.Connection) -> dict:
    """Return a health status dict for the database.

    Returns:
        {
            "ok": bool,
            "migration_version": int,
            "org_count": int,
            "latest_diagnostic_run": str | None,
            "last_alert_eval_age_hours": float | None,
            "alert_eval_stale": bool,
        }
    """
    try:
        version_row = conn.execute("SELECT version FROM schema_version").fetchone()
        migration_version = version_row[0] if version_row else 0
    except sqlite3.OperationalError:
        return {
            "ok": False,
            "migration_version": 0,
            "org_count": 0,
            "latest_diagnostic_run": None,
            "last_alert_eval_age_hours": None,
            "alert_eval_stale": True,
        }

    org_row = conn.execute("SELECT COUNT(*) as cnt FROM orgs").fetchone()
    org_count = org_row["cnt"] if org_row else 0

    diag_row = conn.execute(
        "SELECT MAX(started_at) as latest FROM diagnostic_runs"
    ).fetchone()
    latest_diag = diag_row["latest"] if diag_row else None

    last_eval_age_hours: float | None = None
    alert_eval_stale = True
    try:
        meta_row = conn.execute(
            "SELECT CAST((julianday('now') - julianday(value)) * 24 AS REAL) as age_hours"
            " FROM platform_meta WHERE key='last_alert_eval_at'"
        ).fetchone()
        if meta_row and meta_row["age_hours"] is not None:
            last_eval_age_hours = round(meta_row["age_hours"], 2)
            alert_eval_stale = last_eval_age_hours > 26.0
    except sqlite3.OperationalError:
        pass  # platform_meta table absent on old schema

    return {
        "ok": True,
        "migration_version": migration_version,
        "org_count": org_count,
        "latest_diagnostic_run": latest_diag,
        "last_alert_eval_age_hours": last_eval_age_hours,
        "alert_eval_stale": alert_eval_stale,
    }
