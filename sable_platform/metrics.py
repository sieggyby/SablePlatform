"""Prometheus text format metrics export — stdlib only, no new deps."""
from __future__ import annotations

import sqlite3

from sqlalchemy.exc import OperationalError as SAOperationalError

from sable_platform.db.compat import get_dialect, seconds_since


def export_metrics(conn: sqlite3.Connection) -> str:
    """Return a Prometheus text format string with platform metrics.

    Metrics exported:
      sable_active_orgs                              gauge
      sable_workflow_runs_total{status}              counter
      sable_alerts_total{severity,status}            gauge
      sable_last_alert_eval_age_seconds              gauge  (-1 if never run)
    """
    lines: list[str] = []

    # --- sable_active_orgs ---
    row = conn.execute("SELECT COUNT(*) FROM orgs WHERE status='active'").fetchone()
    active_orgs = row[0] if row else 0
    lines.append("# HELP sable_active_orgs Number of active orgs")
    lines.append("# TYPE sable_active_orgs gauge")
    lines.append(f"sable_active_orgs {active_orgs}")

    # --- sable_workflow_runs_total ---
    lines.append("# HELP sable_workflow_runs_total Total workflow runs by status")
    lines.append("# TYPE sable_workflow_runs_total counter")
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM workflow_runs GROUP BY status"
    ).fetchall()
    for r in rows:
        lines.append(f'sable_workflow_runs_total{{status="{r[0]}"}} {r[1]}')

    # --- sable_alerts_total ---
    lines.append("# HELP sable_alerts_total Current alerts by severity and status")
    lines.append("# TYPE sable_alerts_total gauge")
    rows = conn.execute(
        "SELECT severity, status, COUNT(*) as cnt FROM alerts GROUP BY severity, status"
    ).fetchall()
    for r in rows:
        lines.append(f'sable_alerts_total{{severity="{r[0]}",status="{r[1]}"}} {r[2]}')

    # --- sable_last_alert_eval_age_seconds ---
    lines.append("# HELP sable_last_alert_eval_age_seconds Seconds since last alert evaluation (-1 if never run)")
    lines.append("# TYPE sable_last_alert_eval_age_seconds gauge")
    age_seconds = -1.0
    try:
        _dialect = get_dialect(conn)
        _expr = seconds_since("value", _dialect)
        meta_row = conn.execute(
            f"SELECT CAST({_expr} AS REAL) as age_secs"
            " FROM platform_meta WHERE key='last_alert_eval_at'"
        ).fetchone()
        if meta_row and meta_row[0] is not None:
            age_seconds = round(meta_row[0], 1)
    except (sqlite3.OperationalError, SAOperationalError):
        pass
    lines.append(f"sable_last_alert_eval_age_seconds {age_seconds}")

    return "\n".join(lines) + "\n"
