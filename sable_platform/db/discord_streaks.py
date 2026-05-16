"""DB helpers for discord_streak_events in sable.db."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


def _now_iso_ms() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _row_to_dict(row: Any) -> dict:
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    return dict(row)


def upsert_streak_event(
    conn: Connection,
    org_id: str,
    guild_id: str,
    channel_id: str,
    post_id: str,
    user_id: str,
    posted_at: str,
    counted_for_day: str,
    attachment_count: int,
    image_attachment_count: int,
    ingest_source: str = "gateway",
) -> None:
    """Insert or update on (guild_id, post_id). Never clobbers reaction_score."""
    conn.execute(
        text(
            "INSERT INTO discord_streak_events"
            " (org_id, guild_id, channel_id, post_id, user_id, posted_at, counted_for_day,"
            "  attachment_count, image_attachment_count, ingest_source)"
            " VALUES (:org_id, :guild_id, :channel_id, :post_id, :user_id, :posted_at,"
            "  :counted_for_day, :attachment_count, :image_attachment_count, :ingest_source)"
            " ON CONFLICT (guild_id, post_id) DO UPDATE SET"
            "  updated_at = excluded.updated_at"
        ),
        {
            "org_id": org_id,
            "guild_id": guild_id,
            "channel_id": channel_id,
            "post_id": post_id,
            "user_id": user_id,
            "posted_at": posted_at,
            "counted_for_day": counted_for_day,
            "attachment_count": attachment_count,
            "image_attachment_count": image_attachment_count,
            "ingest_source": ingest_source,
        },
    )
    conn.commit()


def update_reaction_score(
    conn: Connection,
    guild_id: str,
    post_id: str,
    reaction_score: int,
    expected_updated_at: str,
) -> bool:
    """Optimistic-locked UPDATE. Returns True if applied, False if stale."""
    result = conn.execute(
        text(
            "UPDATE discord_streak_events"
            " SET reaction_score = :score, updated_at = :now"
            " WHERE guild_id = :guild_id AND post_id = :post_id"
            "   AND updated_at = :expected"
        ),
        {
            "score": reaction_score,
            "now": _now_iso_ms(),
            "guild_id": guild_id,
            "post_id": post_id,
            "expected": expected_updated_at,
        },
    )
    conn.commit()
    return result.rowcount == 1


def get_event(conn: Connection, guild_id: str, post_id: str) -> dict | None:
    row = conn.execute(
        text(
            "SELECT * FROM discord_streak_events"
            " WHERE guild_id = :guild_id AND post_id = :post_id"
            " LIMIT 1"
        ),
        {"guild_id": guild_id, "post_id": post_id},
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def compute_streak_state(
    conn: Connection,
    org_id: str,
    user_id: str,
    as_of_day: str | None = None,
) -> dict:
    today = date.fromisoformat(as_of_day) if as_of_day else datetime.now(timezone.utc).date()
    rows = conn.execute(
        text(
            "SELECT DISTINCT counted_for_day FROM discord_streak_events"
            " WHERE org_id = :org_id AND user_id = :user_id"
            "   AND counts_for_streak = 1 AND invalidated_at IS NULL"
            " ORDER BY counted_for_day DESC"
        ),
        {"org_id": org_id, "user_id": user_id},
    ).fetchall()
    day_set = {date.fromisoformat(row["counted_for_day"]) for row in rows}

    total_row = conn.execute(
        text(
            "SELECT COUNT(*) AS total FROM discord_streak_events"
            " WHERE org_id = :org_id AND user_id = :user_id"
            "   AND counts_for_streak = 1 AND invalidated_at IS NULL"
        ),
        {"org_id": org_id, "user_id": user_id},
    ).fetchone()
    total_fits = int(total_row["total"]) if total_row is not None else 0

    posted_today = today in day_set
    if posted_today:
        current_anchor = today
    elif today - timedelta(days=1) in day_set:
        current_anchor = today - timedelta(days=1)
    else:
        current_anchor = None

    current_streak = 0
    if current_anchor is not None:
        cursor = current_anchor
        while cursor in day_set:
            current_streak += 1
            cursor -= timedelta(days=1)

    longest_streak = 0
    run = 0
    previous: date | None = None
    for day in sorted(day_set):
        if previous is not None and day == previous + timedelta(days=1):
            run += 1
        else:
            run = 1
        longest_streak = max(longest_streak, run)
        previous = day

    best_row = conn.execute(
        text(
            "SELECT post_id, reaction_score, channel_id, guild_id"
            " FROM discord_streak_events"
            " WHERE org_id = :org_id AND user_id = :user_id"
            "   AND counts_for_streak = 1 AND invalidated_at IS NULL"
            " ORDER BY reaction_score DESC, posted_at DESC, post_id DESC"
            " LIMIT 1"
        ),
        {"org_id": org_id, "user_id": user_id},
    ).fetchone()
    best = _row_to_dict(best_row) if best_row is not None else {}

    today_row = conn.execute(
        text(
            "SELECT post_id, reaction_score FROM discord_streak_events"
            " WHERE org_id = :org_id AND user_id = :user_id"
            "   AND counted_for_day = :today"
            "   AND counts_for_streak = 1 AND invalidated_at IS NULL"
            " ORDER BY posted_at DESC, post_id DESC"
            " LIMIT 1"
        ),
        {"org_id": org_id, "user_id": user_id, "today": today.isoformat()},
    ).fetchone()
    today_event = _row_to_dict(today_row) if today_row is not None else {}

    return {
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "total_fits": total_fits,
        "most_reacted_post_id": best.get("post_id"),
        "most_reacted_reaction_count": best.get("reaction_score", 0),
        "most_reacted_channel_id": best.get("channel_id"),
        "most_reacted_guild_id": best.get("guild_id"),
        "today_post_id": today_event.get("post_id"),
        "today_reaction_count": today_event.get("reaction_score", 0),
        "posted_today": posted_today,
    }


def list_active_streak_users(conn: Connection) -> list[dict]:
    """Enumerate (guild_id, user_id, org_id) tuples that have at least one
    counts_for_streak=1 + non-invalidated fit-event.

    Used by R8's grandfathering CLI to scan every active streak holder
    and grant restoration tokens to those currently at 7-day streaks
    at feature-deploy time. Bounded by distinct active fitters — at
    SolStitch V1 this is single-digits to low-tens. Re-runs are
    idempotent via the SP grant_restoration_token ON CONFLICT.
    """
    rows = conn.execute(
        text(
            "SELECT DISTINCT guild_id, user_id, org_id"
            " FROM discord_streak_events"
            " WHERE counts_for_streak = 1 AND invalidated_at IS NULL"
            " ORDER BY guild_id ASC, user_id ASC"
        )
    ).fetchall()
    return [_row_to_dict(r) for r in rows]
