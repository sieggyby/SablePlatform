"""DB helpers for discord_pulse_runs in sable.db."""
from __future__ import annotations

import sqlite3


def upsert_discord_pulse_run(
    conn: sqlite3.Connection,
    org_id: str,
    project_slug: str,
    run_date: str,
    wow_retention_rate: float | None,
    echo_rate: float | None,
    avg_silence_gap_hours: float | None,
    weekly_active_posters: int | None,
    retention_delta: float | None,
    echo_rate_delta: float | None,
) -> None:
    """Insert or replace a discord pulse run row. Idempotent on (org_id, project_slug, run_date)."""
    conn.execute(
        """INSERT OR REPLACE INTO discord_pulse_runs
           (org_id, project_slug, run_date,
            wow_retention_rate, echo_rate, avg_silence_gap_hours,
            weekly_active_posters, retention_delta, echo_rate_delta)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            org_id, project_slug, run_date,
            wow_retention_rate, echo_rate, avg_silence_gap_hours,
            weekly_active_posters, retention_delta, echo_rate_delta,
        ),
    )
    conn.commit()


def get_discord_pulse_runs(
    conn: sqlite3.Connection,
    org_id: str,
    project_slug: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Return recent pulse run rows for an org, newest first."""
    if project_slug is not None:
        rows = conn.execute(
            """SELECT * FROM discord_pulse_runs
               WHERE org_id = ? AND project_slug = ?
               ORDER BY run_date DESC LIMIT ?""",
            (org_id, project_slug, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM discord_pulse_runs
               WHERE org_id = ?
               ORDER BY run_date DESC LIMIT ?""",
            (org_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
