"""DB helpers for sable-roles V2 burn-me feature.

Two tables back this module (mig 046):
- discord_burn_optins: per (guild_id, user_id) opt-in row carrying mode (once|persist)
  and audit fields. opt_in() upserts; opt_out() deletes. consume_optin_if_present()
  is the read-then-maybe-delete path used by on_message.
- discord_burn_random_log: append-only log of random-bypass roasts. Used for 7d
  per-target dedup of inner-circle random rolls.

Daily-cap counting (count_roasts_today) reads audit_log rows with
action='fitcheck_roast_generated' via the dialect-aware
``compat.json_extract_text`` + ``compat.date_of_iso_text`` helpers so the
query works on both SQLite (local) and Postgres (prod). The R0 chunk of the
roast plan added the helpers; the previous SQLite-only form was a known B1
minor follow-up.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.db.compat import (
    date_of_iso_text,
    get_dialect,
    json_extract_text,
)

VALID_BURN_MODES = ("once", "persist")


def _now_iso_seconds() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def opt_in(
    conn: Connection,
    guild_id: str,
    user_id: str,
    mode: str,
    opted_in_by: str,
) -> None:
    """Upsert an opt-in row for (guild_id, user_id). Replaces mode + opted_in_by on conflict."""
    if mode not in VALID_BURN_MODES:
        raise ValueError(f"mode must be one of {VALID_BURN_MODES}, got {mode!r}")
    conn.execute(
        text(
            "INSERT INTO discord_burn_optins (guild_id, user_id, mode, opted_in_by, opted_in_at)"
            " VALUES (:guild_id, :user_id, :mode, :by, :now)"
            " ON CONFLICT (guild_id, user_id) DO UPDATE SET"
            "  mode = excluded.mode, opted_in_by = excluded.opted_in_by, opted_in_at = excluded.opted_in_at"
        ),
        {
            "guild_id": guild_id,
            "user_id": user_id,
            "mode": mode,
            "by": opted_in_by,
            "now": _now_iso_seconds(),
        },
    )
    conn.commit()


def opt_out(conn: Connection, guild_id: str, user_id: str) -> bool:
    """Delete the opt-in row. Returns True if a row was removed, False otherwise."""
    result = conn.execute(
        text("DELETE FROM discord_burn_optins WHERE guild_id = :guild_id AND user_id = :user_id"),
        {"guild_id": guild_id, "user_id": user_id},
    )
    conn.commit()
    return result.rowcount == 1


def get_optin(conn: Connection, guild_id: str, user_id: str) -> dict | None:
    row = conn.execute(
        text(
            "SELECT guild_id, user_id, mode, opted_in_by, opted_in_at"
            " FROM discord_burn_optins WHERE guild_id = :g AND user_id = :u LIMIT 1"
        ),
        {"g": guild_id, "u": user_id},
    ).fetchone()
    if row is None:
        return None
    return {
        "guild_id": row["guild_id"],
        "user_id": row["user_id"],
        "mode": row["mode"],
        "opted_in_by": row["opted_in_by"],
        "opted_in_at": row["opted_in_at"],
    }


def consume_optin_if_present(conn: Connection, guild_id: str, user_id: str) -> str | None:
    """Atomic-ish: read opt-in mode; if 'once', delete the row.

    Returns the mode that was active ('once' or 'persist') or None if no opt-in.
    Persist mode does NOT delete — the row stays for subsequent images.
    """
    row = get_optin(conn, guild_id, user_id)
    if row is None:
        return None
    mode = row["mode"]
    if mode == "once":
        opt_out(conn, guild_id, user_id)  # commits internally
    return mode


def log_random_roast(conn: Connection, guild_id: str, user_id: str) -> None:
    conn.execute(
        text(
            "INSERT INTO discord_burn_random_log (guild_id, user_id, roasted_at)"
            " VALUES (:g, :u, :now)"
        ),
        {"g": guild_id, "u": user_id, "now": _now_iso_seconds()},
    )
    conn.commit()


def was_recently_random_roasted(
    conn: Connection,
    guild_id: str,
    user_id: str,
    within_days: int = 7,
) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    row = conn.execute(
        text(
            "SELECT 1 FROM discord_burn_random_log"
            " WHERE guild_id = :g AND user_id = :u AND roasted_at > :cutoff LIMIT 1"
        ),
        {"g": guild_id, "u": user_id, "cutoff": cutoff},
    ).fetchone()
    return row is not None


def count_roasts_today(
    conn: Connection,
    guild_id: str,
    user_id: str,
    as_of_utc: datetime | None = None,
) -> int:
    """Daily-cap helper. Counts both opt-in-path and random-path roasts via the audit_log.

    UTC day boundary. Used to enforce the 20-per-UTC-day per-user cap.

    Dialect-aware: SQLite uses ``json_extract`` + ``date(substr(...))``;
    Postgres uses ``(detail_json::jsonb)->>'key'`` + ``(timestamp::timestamp)::date``.
    Same logic, same result, different syntax per backend. The audit_log
    convention is ``$.user_id`` = the roast TARGET (not the actor), which
    matches the burn-me v1 contract and the post-audit /roast plan.
    """
    today = (as_of_utc or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    dialect = get_dialect(conn)
    guild_expr = json_extract_text("detail_json", "guild_id", dialect)
    user_expr = json_extract_text("detail_json", "user_id", dialect)
    day_expr = date_of_iso_text("timestamp", dialect)
    # Postgres `(timestamp::timestamp)::date = :today` needs :today cast to date
    # for the comparison to type-check; SQLite is text-equal-text.
    # AVOID `:today::date` — SA `text()` greedy-binds the identifier and the
    # `::` cast operator splits the param name (parses as `:toda` + `:date`).
    # `CAST(:today AS DATE)` parses cleanly and preserves the bindparam.
    today_cmp = ":today" if dialect == "sqlite" else "CAST(:today AS DATE)"
    row = conn.execute(
        text(
            "SELECT COUNT(*) AS n FROM audit_log"
            " WHERE source = 'sable-roles' AND action = 'fitcheck_roast_generated'"
            f"   AND {guild_expr} = :g"
            f"   AND {user_expr} = :u"
            f"   AND {day_expr} = {today_cmp}"
        ),
        {"g": guild_id, "u": user_id, "today": today},
    ).fetchone()
    return int(row["n"]) if row is not None else 0
