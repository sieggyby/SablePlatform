"""DB helpers for discord_state_pins in sable.db.

State-pin surface (migration 054). One row per (guild_id, characteristic)
tracking the currently-pinned "stitzy state" message id in the per-guild
ops channel.

Two functions:

* :func:`get_state_pin` — read the current pointer for a (guild,
  characteristic) pair. Returns ``None`` when no row exists (first
  rotation of that dimension).
* :func:`upsert_state_pin` — INSERT-or-UPDATE with an optimistic-lock
  guard via ``WHERE updated_at = :expected_updated_at`` on the UPDATE
  branch. Mirrors the
  :func:`sable_platform.db.discord_streaks.update_reaction_score`
  precedent.

Audit/announce is sable-roles-side ONLY (see the state-pin plan §2 —
P15). This module is pure pointer storage; no audit row is written
here, and these helpers must never be called from SablePlatform code.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection


def _now_iso_ms() -> str:
    """Millisecond-resolution ISO Z timestamp for the optimistic-lock
    token (R1-H2 fix). Mirrors :func:`discord_streaks._now_iso_ms`
    precedent — second-resolution would let two writers in the same
    wall-clock second both succeed against the same expected token.
    """
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _row_to_dict(row) -> dict:
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    return dict(row)


def get_state_pin(
    conn: Connection,
    guild_id: str,
    characteristic: str,
) -> dict | None:
    """Return the current state-pin pointer for (guild, characteristic).

    Returns a dict with keys ``channel_id``, ``message_id``, ``posted_at``,
    ``updated_at`` — or ``None`` when no row exists. ``updated_at`` is
    the optimistic-lock token the caller passes back as
    ``expected_updated_at`` on the next :func:`upsert_state_pin` call.
    """
    row = conn.execute(
        text(
            "SELECT channel_id, message_id, posted_at, updated_at"
            " FROM discord_state_pins"
            " WHERE guild_id = :guild_id AND characteristic = :characteristic"
            " LIMIT 1"
        ),
        {"guild_id": guild_id, "characteristic": characteristic},
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def upsert_state_pin(
    conn: Connection,
    guild_id: str,
    characteristic: str,
    channel_id: str,
    message_id: str,
    posted_at: str,
    *,
    expected_updated_at: str | None = None,
) -> bool:
    """INSERT-or-UPDATE the state-pin pointer for (guild, characteristic).

    Optimistic lock: when ``expected_updated_at`` is not None, the UPDATE
    branch carries a ``WHERE updated_at = :expected`` clause; if another
    writer landed first the rowcount is 0 and this returns ``False`` —
    caller should treat that as a lost race and clean up its
    just-posted pin (state-pin plan §6.1 step h).

    ``expected_updated_at=None`` skips the optimistic lock and overwrites
    unconditionally. Used for the first-ever pin on a (guild,
    characteristic) where no prior row exists.

    Returns ``True`` if the row was applied (insert OR successful update),
    ``False`` on optimistic-lock loss. Commits before returning.
    """
    now = _now_iso_ms()
    if expected_updated_at is None:
        # First-ever pin OR no-lock-required overwrite. UPSERT
        # unconditionally — the UPDATE branch carries no WHERE-on-
        # updated_at predicate so rowcount is always >=1.
        conn.execute(
            text(
                "INSERT INTO discord_state_pins"
                " (guild_id, characteristic, channel_id, message_id,"
                "  posted_at, updated_at)"
                " VALUES (:guild_id, :characteristic, :channel_id,"
                "  :message_id, :posted_at, :now)"
                " ON CONFLICT (guild_id, characteristic) DO UPDATE SET"
                "  channel_id = excluded.channel_id,"
                "  message_id = excluded.message_id,"
                "  posted_at = excluded.posted_at,"
                "  updated_at = excluded.updated_at"
            ),
            {
                "guild_id": guild_id,
                "characteristic": characteristic,
                "channel_id": channel_id,
                "message_id": message_id,
                "posted_at": posted_at,
                "now": now,
            },
        )
        conn.commit()
        return True

    # Optimistic-locked path. UPDATE only — caller is responsible for
    # having read a prior row (which is why expected_updated_at is non-
    # None). A losing race lands rowcount=0.
    result = conn.execute(
        text(
            "UPDATE discord_state_pins"
            " SET channel_id = :channel_id,"
            "     message_id = :message_id,"
            "     posted_at = :posted_at,"
            "     updated_at = :now"
            " WHERE guild_id = :guild_id"
            "   AND characteristic = :characteristic"
            "   AND updated_at = :expected"
        ),
        {
            "channel_id": channel_id,
            "message_id": message_id,
            "posted_at": posted_at,
            "now": now,
            "guild_id": guild_id,
            "characteristic": characteristic,
            "expected": expected_updated_at,
        },
    )
    conn.commit()
    return result.rowcount == 1
