"""DB helpers for discord_pulse_runs in sable.db."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection


def upsert_discord_pulse_run(
    conn: Connection,
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
        text(
            "INSERT OR REPLACE INTO discord_pulse_runs"
            " (org_id, project_slug, run_date,"
            "  wow_retention_rate, echo_rate, avg_silence_gap_hours,"
            "  weekly_active_posters, retention_delta, echo_rate_delta)"
            " VALUES (:org_id, :project_slug, :run_date,"
            "  :wow_retention_rate, :echo_rate, :avg_silence_gap_hours,"
            "  :weekly_active_posters, :retention_delta, :echo_rate_delta)"
        ),
        {
            "org_id": org_id,
            "project_slug": project_slug,
            "run_date": run_date,
            "wow_retention_rate": wow_retention_rate,
            "echo_rate": echo_rate,
            "avg_silence_gap_hours": avg_silence_gap_hours,
            "weekly_active_posters": weekly_active_posters,
            "retention_delta": retention_delta,
            "echo_rate_delta": echo_rate_delta,
        },
    )
    conn.commit()


def get_discord_pulse_runs(
    conn: Connection,
    org_id: str,
    project_slug: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Return recent pulse run rows for an org, newest first."""
    if project_slug is not None:
        rows = conn.execute(
            text(
                "SELECT * FROM discord_pulse_runs"
                " WHERE org_id = :org_id AND project_slug = :slug"
                " ORDER BY run_date DESC LIMIT :lim"
            ),
            {"org_id": org_id, "slug": project_slug, "lim": limit},
        ).fetchall()
    else:
        rows = conn.execute(
            text(
                "SELECT * FROM discord_pulse_runs"
                " WHERE org_id = :org_id"
                " ORDER BY run_date DESC LIMIT :lim"
            ),
            {"org_id": org_id, "lim": limit},
        ).fetchall()
    return [dict(r) for r in rows]
