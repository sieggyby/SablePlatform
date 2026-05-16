"""DB helpers for sable-roles V2 /roast peer-economy + flag log (mig 047).

Three tables back this module:

* ``discord_burn_blocklist`` (3.1) — sticky /stop-pls opt-out list. Read on
  every /roast attempt (mod or peer) and on every vibe observation/inference
  write. Insertions are idempotent via ``ON CONFLICT DO NOTHING``.

* ``discord_peer_roast_tokens`` (3.2) — peer-economy token ledger. Monthly
  + streak-restoration sources. ``UNIQUE(guild_id, actor_user_id,
  year_month, source)`` blocks the concurrent double-grant race; grants
  use ``ON CONFLICT DO NOTHING``. Consume / refund go through
  ``UPDATE ... WHERE id = :id AND consumed_at IS NULL`` so the second of
  two racing consumes cleanly no-ops.

* ``discord_peer_roast_flags`` (3.3) — peer-roast 🚩 flag log.
  ``reactor_user_id`` distinguishes target-self flags from third-party
  flags; ``bot_reply_id`` resolves attribution when mod + peer roasts
  share the same target fit post_id.

``aggregate_peer_roast_report`` queries the audit_log via the dialect-aware
``compat.json_extract_text`` + ``compat.date_of_iso_text`` helpers so the
same SQL runs on SQLite (local) and Postgres (prod) — same pattern as
``count_roasts_today`` in :mod:`sable_platform.db.discord_burn`.
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

VALID_TOKEN_SOURCES = ("monthly", "streak_restoration")


def _now_iso_seconds() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _current_year_month(as_of_utc: datetime | None = None) -> str:
    return (as_of_utc or datetime.now(timezone.utc)).strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Blocklist (3.1)
# ---------------------------------------------------------------------------


def is_blocklisted(conn: Connection, guild_id: str, user_id: str) -> bool:
    """True iff (guild_id, user_id) has a row in discord_burn_blocklist."""
    row = conn.execute(
        text(
            "SELECT 1 FROM discord_burn_blocklist"
            " WHERE guild_id = :g AND user_id = :u LIMIT 1"
        ),
        {"g": guild_id, "u": user_id},
    ).fetchone()
    return row is not None


def insert_blocklist(conn: Connection, guild_id: str, user_id: str) -> bool:
    """Add (guild_id, user_id) to the sticky stop-pls blocklist.

    Idempotent via UNIQUE(guild_id, user_id) + ON CONFLICT DO NOTHING.
    Returns True if a new row landed, False if the user was already blocked.
    """
    result = conn.execute(
        text(
            "INSERT INTO discord_burn_blocklist (guild_id, user_id, blocked_at)"
            " VALUES (:g, :u, :now)"
            " ON CONFLICT (guild_id, user_id) DO NOTHING"
        ),
        {"g": guild_id, "u": user_id, "now": _now_iso_seconds()},
    )
    conn.commit()
    return result.rowcount == 1


def delete_blocklist(conn: Connection, guild_id: str, user_id: str) -> bool:
    """Remove the blocklist entry. Returns True if a row was removed."""
    result = conn.execute(
        text(
            "DELETE FROM discord_burn_blocklist"
            " WHERE guild_id = :g AND user_id = :u"
        ),
        {"g": guild_id, "u": user_id},
    )
    conn.commit()
    return result.rowcount == 1


def list_blocklisted_users(conn: Connection, guild_id: str) -> list[str]:
    """Return all user_ids on the blocklist for a guild, oldest-first."""
    rows = conn.execute(
        text(
            "SELECT user_id FROM discord_burn_blocklist"
            " WHERE guild_id = :g ORDER BY blocked_at ASC, id ASC"
        ),
        {"g": guild_id},
    ).fetchall()
    return [r["user_id"] for r in rows]


# ---------------------------------------------------------------------------
# Tokens (3.2)
# ---------------------------------------------------------------------------


def _grant_token(
    conn: Connection,
    guild_id: str,
    actor_user_id: str,
    source: str,
    year_month: str,
) -> bool:
    if source not in VALID_TOKEN_SOURCES:
        raise ValueError(
            f"source must be one of {VALID_TOKEN_SOURCES}, got {source!r}"
        )
    result = conn.execute(
        text(
            "INSERT INTO discord_peer_roast_tokens"
            " (guild_id, actor_user_id, source, year_month, granted_at)"
            " VALUES (:g, :a, :s, :ym, :now)"
            " ON CONFLICT (guild_id, actor_user_id, year_month, source) DO NOTHING"
        ),
        {
            "g": guild_id,
            "a": actor_user_id,
            "s": source,
            "ym": year_month,
            "now": _now_iso_seconds(),
        },
    )
    conn.commit()
    return result.rowcount == 1


def grant_monthly_token(
    conn: Connection,
    guild_id: str,
    actor_user_id: str,
    *,
    year_month: str | None = None,
) -> bool:
    """Lazy-grant the monthly peer-roast token. ON CONFLICT DO NOTHING
    blocks the concurrent-double-grant race (post-audit BLOCKER 2).

    Returns True if a new token row was inserted, False if a row for
    (guild_id, actor_user_id, year_month, 'monthly') already existed.
    Callers should follow up with :func:`available_token` to retrieve
    the unspent row id (the existing row may or may not still be
    available depending on whether it was already consumed).
    """
    return _grant_token(
        conn, guild_id, actor_user_id, "monthly",
        year_month or _current_year_month(),
    )


def grant_restoration_token(
    conn: Connection,
    guild_id: str,
    actor_user_id: str,
    *,
    year_month: str | None = None,
) -> bool:
    """Grant the streak-restoration bonus token. Same race guard as
    :func:`grant_monthly_token`. Multiple monthly + restoration grants
    can coexist in the same calendar month (different ``source`` values
    satisfy the unique constraint).
    """
    return _grant_token(
        conn, guild_id, actor_user_id, "streak_restoration",
        year_month or _current_year_month(),
    )


def available_token(
    conn: Connection,
    guild_id: str,
    actor_user_id: str,
    *,
    year_month: str | None = None,
) -> dict | None:
    """Return the oldest unspent token for (guild_id, actor_user_id, year_month).

    Implements plan §0.2: a token is "available" when its
    ``consumed_at IS NULL`` for the current year_month. Returns ``None``
    if no token has been granted yet, or all grants have been spent.
    """
    ym = year_month or _current_year_month()
    row = conn.execute(
        text(
            "SELECT id, guild_id, actor_user_id, source, year_month,"
            "       granted_at, consumed_at, consumed_on_post_id,"
            "       consumed_target_user_id"
            " FROM discord_peer_roast_tokens"
            " WHERE guild_id = :g AND actor_user_id = :a"
            "   AND year_month = :ym AND consumed_at IS NULL"
            " ORDER BY granted_at ASC, id ASC"
            " LIMIT 1"
        ),
        {"g": guild_id, "a": actor_user_id, "ym": ym},
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "guild_id": row["guild_id"],
        "actor_user_id": row["actor_user_id"],
        "source": row["source"],
        "year_month": row["year_month"],
        "granted_at": row["granted_at"],
        "consumed_at": row["consumed_at"],
        "consumed_on_post_id": row["consumed_on_post_id"],
        "consumed_target_user_id": row["consumed_target_user_id"],
    }


def count_available_tokens(
    conn: Connection,
    guild_id: str,
    actor_user_id: str,
    *,
    year_month: str | None = None,
) -> int:
    """Count unspent tokens for (guild_id, actor_user_id, year_month).

    Used by /my-roasts to render the actor's token balance.
    """
    ym = year_month or _current_year_month()
    row = conn.execute(
        text(
            "SELECT COUNT(*) AS n FROM discord_peer_roast_tokens"
            " WHERE guild_id = :g AND actor_user_id = :a"
            "   AND year_month = :ym AND consumed_at IS NULL"
        ),
        {"g": guild_id, "a": actor_user_id, "ym": ym},
    ).fetchone()
    return int(row["n"]) if row is not None else 0


def last_consumed_token(
    conn: Connection,
    guild_id: str,
    actor_user_id: str,
) -> dict | None:
    """Return the most-recently consumed token row for (guild_id, actor_user_id).

    Inverse of :func:`available_token`: that helper filters
    ``consumed_at IS NULL``; this one filters ``consumed_at IS NOT NULL``
    and orders by ``consumed_at DESC, id DESC`` so the freshest consumption
    wins (``id`` tiebreak handles the rare case where two consumptions
    share an iso-seconds timestamp).

    Used by ``/my-roasts`` to render the actor's "last roast cast" line
    (date + target_user_id). Row shape matches :func:`available_token`
    byte-for-byte so downstream renderers can consume either helper's
    output uniformly.

    Returns ``None`` if the actor has never spent a token in this guild.
    """
    row = conn.execute(
        text(
            "SELECT id, guild_id, actor_user_id, source, year_month,"
            "       granted_at, consumed_at, consumed_on_post_id,"
            "       consumed_target_user_id"
            " FROM discord_peer_roast_tokens"
            " WHERE guild_id = :g AND actor_user_id = :a"
            "   AND consumed_at IS NOT NULL"
            " ORDER BY consumed_at DESC, id DESC"
            " LIMIT 1"
        ),
        {"g": guild_id, "a": actor_user_id},
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "guild_id": row["guild_id"],
        "actor_user_id": row["actor_user_id"],
        "source": row["source"],
        "year_month": row["year_month"],
        "granted_at": row["granted_at"],
        "consumed_at": row["consumed_at"],
        "consumed_on_post_id": row["consumed_on_post_id"],
        "consumed_target_user_id": row["consumed_target_user_id"],
    }


def consume_token(
    conn: Connection,
    token_id: int,
    *,
    target_user_id: str,
    post_id: str,
) -> bool:
    """Mark a token as consumed against (target_user_id, post_id).

    Atomic — uses ``WHERE id = :id AND consumed_at IS NULL`` so a second
    racing consume on the same row cleanly returns False. Returns True
    when consumption succeeded.
    """
    result = conn.execute(
        text(
            "UPDATE discord_peer_roast_tokens"
            " SET consumed_at = :now,"
            "     consumed_target_user_id = :target,"
            "     consumed_on_post_id = :post"
            " WHERE id = :id AND consumed_at IS NULL"
        ),
        {
            "id": token_id,
            "target": target_user_id,
            "post": post_id,
            "now": _now_iso_seconds(),
        },
    )
    conn.commit()
    return result.rowcount == 1


def refund_token(conn: Connection, token_id: int) -> bool:
    """Reset a token to unspent — used on bounce-after-consume + LLM refusal.

    Clears consumed_at + consumed_target_user_id + consumed_on_post_id so
    the row looks freshly granted (no audit residue, per plan §5.2).
    Returns True when a row was updated (was previously consumed).
    """
    result = conn.execute(
        text(
            "UPDATE discord_peer_roast_tokens"
            " SET consumed_at = NULL,"
            "     consumed_target_user_id = NULL,"
            "     consumed_on_post_id = NULL"
            " WHERE id = :id AND consumed_at IS NOT NULL"
        ),
        {"id": token_id},
    )
    conn.commit()
    return result.rowcount == 1


def count_target_peer_roasts_this_month(
    conn: Connection,
    guild_id: str,
    target_user_id: str,
    *,
    year_month: str | None = None,
    as_of_utc: datetime | None = None,
) -> int:
    """Count peer-roast tokens consumed against this target in the
    calendar month identified by ``year_month`` (defaults to current UTC).

    The cap query filters on ``consumed_at`` — NOT the grant-month
    ``year_month`` column — because a January-granted token spent in
    February counts toward February's cap from the target's perspective.
    The ``year_month`` column on the token row reflects when it was
    granted (and pins UNIQUE for the monthly-grant gate); consumption
    timing is what the per-target volume cap (3/month, inner-circle
    bypass) keys on.
    """
    ym = year_month or _current_year_month(as_of_utc)
    # ISO-text comparison works on both SQLite (TEXT) and Postgres
    # (TIMESTAMP implicit cast from ISO-8601 string).
    cutoff_start = f"{ym}-01T00:00:00Z"
    # Compute first day of the following month for an exclusive upper bound.
    year_int, month_int = (int(part) for part in ym.split("-"))
    if month_int == 12:
        next_ym = f"{year_int + 1:04d}-01"
    else:
        next_ym = f"{year_int:04d}-{month_int + 1:02d}"
    cutoff_end = f"{next_ym}-01T00:00:00Z"
    row = conn.execute(
        text(
            "SELECT COUNT(*) AS n FROM discord_peer_roast_tokens"
            " WHERE guild_id = :g AND consumed_target_user_id = :t"
            "   AND consumed_at IS NOT NULL"
            "   AND consumed_at >= :start AND consumed_at < :end"
        ),
        {
            "g": guild_id,
            "t": target_user_id,
            "start": cutoff_start,
            "end": cutoff_end,
        },
    ).fetchone()
    return int(row["n"]) if row is not None else 0


def cooldown_active_between(
    conn: Connection,
    guild_id: str,
    actor_user_id: str,
    target_user_id: str,
    *,
    within_days: int = 90,
    as_of_utc: datetime | None = None,
) -> bool:
    """True iff this actor has consumed a peer-roast token against this
    target within the last ``within_days``. Used for the per-actor-per-
    target cooldown gate (inner-circle bypass at the application layer).
    """
    cutoff = (
        (as_of_utc or datetime.now(timezone.utc)) - timedelta(days=within_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute(
        text(
            "SELECT 1 FROM discord_peer_roast_tokens"
            " WHERE guild_id = :g AND actor_user_id = :a"
            "   AND consumed_target_user_id = :t"
            "   AND consumed_at IS NOT NULL AND consumed_at > :cutoff"
            " LIMIT 1"
        ),
        {"g": guild_id, "a": actor_user_id, "t": target_user_id, "cutoff": cutoff},
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Flags (3.3)
# ---------------------------------------------------------------------------


def insert_flag(
    conn: Connection,
    *,
    guild_id: str,
    target_user_id: str,
    actor_user_id: str,
    post_id: str,
    bot_reply_id: str,
    reactor_user_id: str,
) -> int:
    """Insert a 🚩 flag row. Returns the new id."""
    result = conn.execute(
        text(
            "INSERT INTO discord_peer_roast_flags"
            " (guild_id, target_user_id, actor_user_id, post_id,"
            "  bot_reply_id, reactor_user_id, flagged_at)"
            " VALUES (:g, :t, :a, :post, :reply, :reactor, :now)"
            " RETURNING id"
        ),
        {
            "g": guild_id,
            "t": target_user_id,
            "a": actor_user_id,
            "post": post_id,
            "reply": bot_reply_id,
            "reactor": reactor_user_id,
            "now": _now_iso_seconds(),
        },
    ).fetchone()
    conn.commit()
    return int(result["id"])


def list_flags(
    conn: Connection,
    guild_id: str,
    *,
    target_user_id: str | None = None,
    within_days: int | None = None,
    as_of_utc: datetime | None = None,
) -> list[dict]:
    """Return flag rows for a guild, optionally filtered.

    Used by /peer-roast-report to enumerate flagged roasts.
    """
    sql = (
        "SELECT id, guild_id, target_user_id, actor_user_id, post_id,"
        "       bot_reply_id, reactor_user_id, flagged_at"
        " FROM discord_peer_roast_flags"
        " WHERE guild_id = :g"
    )
    params: dict = {"g": guild_id}
    if target_user_id is not None:
        sql += " AND target_user_id = :t"
        params["t"] = target_user_id
    if within_days is not None:
        cutoff = (
            (as_of_utc or datetime.now(timezone.utc)) - timedelta(days=within_days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        sql += " AND flagged_at > :cutoff"
        params["cutoff"] = cutoff
    sql += " ORDER BY flagged_at DESC, id DESC"
    rows = conn.execute(text(sql), params).fetchall()
    return [
        {
            "id": r["id"],
            "guild_id": r["guild_id"],
            "target_user_id": r["target_user_id"],
            "actor_user_id": r["actor_user_id"],
            "post_id": r["post_id"],
            "bot_reply_id": r["bot_reply_id"],
            "reactor_user_id": r["reactor_user_id"],
            "flagged_at": r["flagged_at"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Flag-resolution helper (§8.2) — used by sable-roles 🚩 reaction handler
# ---------------------------------------------------------------------------


def find_peer_roast_for_bot_reply(
    conn: Connection,
    bot_reply_id: str,
) -> dict | None:
    """Resolve a bot-reply message id back to the originating peer-roast
    audit row. Returns a dict with target_user_id / actor_user_id /
    post_id / guild_id if the bot_reply_id corresponds to a peer-roast
    (or peer_roast_restored) reply, else None.

    Two-step lookup against ``audit_log``:

    1. Find the `fitcheck_roast_replied` row whose ``detail_json.bot_reply_id``
       matches the supplied id.
    2. Look up the ``fitcheck_roast_generated`` row by its primary key
       (carried in ``detail_json.audit_log_id`` on the replied row).
       Confirm ``invocation_path`` is peer_roast or peer_roast_restored —
       opt-in / random / mod-roast replies are silently ignored.

    Dialect-aware via the same :func:`compat.json_extract_text` pattern as
    :func:`aggregate_peer_roast_report`. None means "not a tracked peer-
    roast reply" — the 🚩 reaction handler treats that as silent ignore.
    """
    dialect = get_dialect(conn)
    reply_bot_id_expr = json_extract_text("detail_json", "bot_reply_id", dialect)
    reply_audit_id_expr = json_extract_text("detail_json", "audit_log_id", dialect)
    invocation_expr = json_extract_text("detail_json", "invocation_path", dialect)
    target_expr = json_extract_text("detail_json", "user_id", dialect)
    actor_expr = json_extract_text("detail_json", "actor_user_id", dialect)
    post_expr = json_extract_text("detail_json", "post_id", dialect)
    guild_expr = json_extract_text("detail_json", "guild_id", dialect)
    if dialect == "sqlite":
        cast_id = "CAST(gen.id AS TEXT)"
    else:
        cast_id = "gen.id::text"
    # Bare `detail_json` resolves unambiguously inside each table-aliased
    # scope (outer query: gen.detail_json; subquery: rep.detail_json).
    sql = (
        "SELECT "
        f"  {target_expr} AS target_user_id,"
        f"  {actor_expr} AS actor_user_id,"
        f"  {post_expr} AS post_id,"
        f"  {guild_expr} AS guild_id,"
        f"  {invocation_expr} AS invocation_path"
        " FROM audit_log gen"
        " WHERE gen.action = 'fitcheck_roast_generated'"
        "   AND gen.source = 'sable-roles'"
        f"   AND {invocation_expr} IN ('peer_roast', 'peer_roast_restored')"
        f"   AND {cast_id} = ("
        "       SELECT "
        f"         {reply_audit_id_expr}"
        "       FROM audit_log rep"
        "       WHERE rep.action = 'fitcheck_roast_replied'"
        "         AND rep.source = 'sable-roles'"
        f"         AND {reply_bot_id_expr} = :bot_reply"
        "       ORDER BY rep.id DESC LIMIT 1"
        "   )"
        " LIMIT 1"
    )
    row = conn.execute(text(sql), {"bot_reply": bot_reply_id}).fetchone()
    if row is None:
        return None
    return {
        "target_user_id": row["target_user_id"],
        "actor_user_id": row["actor_user_id"],
        "post_id": row["post_id"],
        "guild_id": row["guild_id"],
        "invocation_path": row["invocation_path"],
    }


# ---------------------------------------------------------------------------
# Aggregate report (§8.3)
# ---------------------------------------------------------------------------


def aggregate_peer_roast_report(
    conn: Connection,
    guild_id: str,
    *,
    lookback_days: int = 30,
    as_of_utc: datetime | None = None,
) -> list[dict]:
    """Aggregate peer-roast activity for /peer-roast-report.

    Joins the audit_log (fitcheck_roast_generated rows for peer paths)
    to the reply-link audit (fitcheck_roast_replied — carries
    audit_log_id + bot_reply_id) and the flag log, then groups by
    (actor_user_id, target_user_id).

    Dialect-aware via :func:`compat.json_extract_text` so the same SQL
    runs on SQLite (local) and Postgres (prod). The audit detail
    convention (target = ``$.user_id`` per the burn-me contract; actor =
    ``$.actor_user_id``; reply linkage = ``$.audit_log_id``) is enforced
    by the upstream writers in :mod:`sable_roles`.

    Returns a list of dicts: ``{actor_user_id, target_user_id, n,
    flag_count, self_flag_count}``, sorted by ``flag_count DESC, n DESC``.
    """
    dialect = get_dialect(conn)
    guild_expr = json_extract_text("detail_json", "guild_id", dialect)
    user_expr = json_extract_text("detail_json", "user_id", dialect)
    actor_expr = json_extract_text("detail_json", "actor_user_id", dialect)
    invocation_expr = json_extract_text("detail_json", "invocation_path", dialect)
    audit_link_expr = json_extract_text("detail_json", "audit_log_id", dialect)
    bot_reply_expr = json_extract_text("detail_json", "bot_reply_id", dialect)
    day_expr = date_of_iso_text("timestamp", dialect)
    # JSON-extracted values come back as text on both dialects (jsonb->>)
    # so we compare them as text — cast audit_log.id to text on the join.
    if dialect == "sqlite":
        cast_id = "CAST(al.id AS TEXT)"
        cutoff_cmp = ":cutoff"
    else:
        cast_id = "al.id::text"
        cutoff_cmp = "CAST(:cutoff AS DATE)"
    cutoff = (
        (as_of_utc or datetime.now(timezone.utc)) - timedelta(days=lookback_days)
    ).strftime("%Y-%m-%d")
    sql = (
        "WITH peer_roasts AS ("
        "  SELECT "
        f"   {cast_id} AS audit_log_id,"
        f"   {user_expr} AS target_user_id,"
        f"   {actor_expr} AS actor_user_id,"
        "    al.timestamp AS ts"
        "  FROM audit_log al"
        "  WHERE al.action = 'fitcheck_roast_generated'"
        "    AND al.source = 'sable-roles'"
        f"    AND {invocation_expr} IN ('peer_roast', 'peer_roast_restored')"
        f"    AND {guild_expr} = :guild_id"
        f"    AND {day_expr} >= {cutoff_cmp}"
        "), reply_map AS ("
        "  SELECT "
        f"   {audit_link_expr} AS source_audit_id,"
        f"   {bot_reply_expr} AS bot_reply_id"
        "  FROM audit_log al"
        "  WHERE al.action = 'fitcheck_roast_replied'"
        "    AND al.source = 'sable-roles'"
        ")"
        " SELECT pr.actor_user_id AS actor_user_id,"
        "        pr.target_user_id AS target_user_id,"
        "        COUNT(*) AS n,"
        "        SUM(CASE WHEN f.id IS NOT NULL THEN 1 ELSE 0 END) AS flag_count,"
        "        SUM(CASE WHEN f.reactor_user_id = pr.target_user_id"
        "                 THEN 1 ELSE 0 END) AS self_flag_count"
        " FROM peer_roasts pr"
        " LEFT JOIN reply_map rm ON rm.source_audit_id = pr.audit_log_id"
        " LEFT JOIN discord_peer_roast_flags f"
        "        ON f.bot_reply_id = rm.bot_reply_id AND f.guild_id = :guild_id"
        " GROUP BY pr.actor_user_id, pr.target_user_id"
        " ORDER BY flag_count DESC, n DESC"
    )
    rows = conn.execute(
        text(sql),
        {"guild_id": guild_id, "cutoff": cutoff},
    ).fetchall()
    return [
        {
            "actor_user_id": r["actor_user_id"],
            "target_user_id": r["target_user_id"],
            "n": int(r["n"]),
            "flag_count": int(r["flag_count"] or 0),
            "self_flag_count": int(r["self_flag_count"] or 0),
        }
        for r in rows
    ]
