"""DB helpers for the sable-roles airlock feature (mig 048).

Three tables back this module:

* ``discord_invite_snapshot`` (2.1) — bot-local cache of every guild
  invite's `uses` count + inviter + metadata. The on_member_join handler
  diffs this against a fresh ``guild.invites()`` call to attribute the
  new join to a specific invite code → its inviter. UPSERT-keyed by
  ``UNIQUE(guild_id, code)``.

* ``discord_team_inviters`` (2.2) — operator-managed allowlist of Sable
  team Discord user-IDs. Members invited by anyone in this list bypass
  airlock and auto-admit to the default member role. Past invites
  grandfather — removing a user does NOT retroactively invalidate
  invites they already created (the attribution lookup checks the
  CURRENT allowlist at join-time, so removing-then-rejoining means the
  next join from the same invite-code gets airlocked).

* ``discord_member_admit`` (2.3) — per-join ledger with a state machine
  (``held`` / ``auto_admitted`` / ``admitted`` / ``banned`` / ``kicked``
  / ``left_during_airlock``). ``UNIQUE(guild_id, user_id)`` so rejoin
  overwrites the prior row via ON CONFLICT DO UPDATE.

All mutation helpers commit immediately (Sable convention).
Diff/attribution logic lives here so the sable-roles handler stays thin
and the same JSON-extract / dialect dance the rest of SP uses applies.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection


VALID_AIRLOCK_STATUSES = (
    "held",
    "auto_admitted",
    "admitted",
    "banned",
    "kicked",
    "left_during_airlock",
)


def _now_iso_seconds() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Invite snapshot (2.1)
# ---------------------------------------------------------------------------


def upsert_invite_snapshot(
    conn: Connection,
    *,
    guild_id: str,
    code: str,
    inviter_user_id: str | None,
    uses: int,
    max_uses: int,
    expires_at: str | None,
) -> None:
    """Insert or update a single invite row keyed on (guild_id, code).

    Called per-invite when refreshing the snapshot (boot + on_invite_create
    + on_invite_delete + post-member_join). uses + max_uses always
    overwrite; inviter_user_id rarely changes but covered by the UPSERT
    in case Discord ever returns a different value.
    """
    conn.execute(
        text(
            "INSERT INTO discord_invite_snapshot"
            " (guild_id, code, inviter_user_id, uses, max_uses, expires_at, captured_at)"
            " VALUES (:g, :c, :i, :u, :m, :e, :now)"
            " ON CONFLICT (guild_id, code) DO UPDATE SET"
            "   inviter_user_id = excluded.inviter_user_id,"
            "   uses = excluded.uses,"
            "   max_uses = excluded.max_uses,"
            "   expires_at = excluded.expires_at,"
            "   captured_at = excluded.captured_at"
        ),
        {
            "g": guild_id,
            "c": code,
            "i": inviter_user_id,
            "u": int(uses),
            "m": int(max_uses),
            "e": expires_at,
            "now": _now_iso_seconds(),
        },
    )
    conn.commit()


def get_invite_snapshot(conn: Connection, guild_id: str) -> dict[str, dict]:
    """Return the snapshot for a guild as ``{code: {uses, inviter_user_id, ...}}``.

    Caller diffs against a fresh ``guild.invites()`` to attribute joins.
    """
    rows = conn.execute(
        text(
            "SELECT code, inviter_user_id, uses, max_uses, expires_at, captured_at"
            " FROM discord_invite_snapshot WHERE guild_id = :g"
        ),
        {"g": guild_id},
    ).fetchall()
    return {
        r["code"]: {
            "code": r["code"],
            "inviter_user_id": r["inviter_user_id"],
            "uses": int(r["uses"]),
            "max_uses": int(r["max_uses"]),
            "expires_at": r["expires_at"],
            "captured_at": r["captured_at"],
        }
        for r in rows
    }


def delete_invite_snapshot(
    conn: Connection,
    *,
    guild_id: str,
    code: str,
) -> bool:
    """Drop a single invite row (used on on_invite_delete). Returns True
    if a row was actually deleted."""
    result = conn.execute(
        text(
            "DELETE FROM discord_invite_snapshot"
            " WHERE guild_id = :g AND code = :c"
        ),
        {"g": guild_id, "c": code},
    )
    conn.commit()
    return result.rowcount == 1


def attribute_join(
    conn: Connection,
    *,
    guild_id: str,
    fresh_invites: list[dict],
) -> dict | None:
    """Diff the live ``fresh_invites`` snapshot against the stored one,
    return the single invite (as a dict) whose ``uses`` incremented OR
    that disappeared due to ``max_uses`` hit.

    Returns ``{"code": str, "inviter_user_id": str | None, ...}`` on
    unambiguous attribution. Returns ``None`` on:

    * No invite changed (vanity-URL join or external attack path)
    * Multiple invites changed in the same call (concurrent joins)
    * The disappeared-invite was never in our snapshot (bot was offline
      when it was created + consumed in one go)

    Caller MUST treat ``None`` as "fail-closed → airlock" per plan §0.4.
    Does NOT mutate the snapshot — caller is responsible for calling
    :func:`upsert_invite_snapshot` per-row after attribution to refresh
    state for the next join.

    ``fresh_invites`` shape: each dict carries ``code``, ``inviter_user_id``
    (or None), ``uses``, ``max_uses``, ``expires_at`` (or None).
    """
    stored = get_invite_snapshot(conn, guild_id)
    fresh_by_code = {row["code"]: row for row in fresh_invites}

    incremented: list[dict] = []
    new_codes: list[dict] = []
    for code, fresh_row in fresh_by_code.items():
        prior = stored.get(code)
        if prior is None:
            # New invite seen for the first time. Two scenarios:
            #  (a) We missed an on_invite_create event (intent gating,
            #      permission gap at creation time, gateway hiccup).
            #  (b) Someone joined immediately on creation, before the
            #      event could be processed.
            # In both cases the inviter is reliably recorded by Discord
            # at creation time, so attribution is safe IFF this is the
            # ONLY change candidate. Below we union with incremented +
            # disappeared, then require len==1.
            if int(fresh_row.get("uses", 0)) > 0:
                new_codes.append(fresh_row)
            continue
        if int(fresh_row["uses"]) > int(prior["uses"]):
            incremented.append(fresh_row)

    # max_uses-consumed invites disappear from the fresh list entirely.
    disappeared: list[dict] = []
    for code, prior in stored.items():
        if code in fresh_by_code:
            continue
        # Only "disappeared because hit max_uses" is a legitimate
        # attribution signal — verify by checking max_uses + uses
        # would have crossed.
        prior_uses = int(prior["uses"])
        prior_max = int(prior["max_uses"])
        if prior_max > 0 and prior_uses + 1 >= prior_max:
            disappeared.append(prior)

    candidates = incremented + disappeared + new_codes
    if len(candidates) != 1:
        return None
    return candidates[0]


# ---------------------------------------------------------------------------
# Team inviters (2.2)
# ---------------------------------------------------------------------------


def add_team_inviter(
    conn: Connection,
    *,
    guild_id: str,
    user_id: str,
    added_by: str,
) -> bool:
    """Add a Discord user-id to the team-inviter allowlist for a guild.

    Idempotent via UNIQUE(guild_id, user_id) + ON CONFLICT DO NOTHING.
    Returns True if a new row landed, False if the user was already on
    the allowlist.
    """
    result = conn.execute(
        text(
            "INSERT INTO discord_team_inviters (guild_id, user_id, added_by, added_at)"
            " VALUES (:g, :u, :by, :now)"
            " ON CONFLICT (guild_id, user_id) DO NOTHING"
        ),
        {
            "g": guild_id,
            "u": user_id,
            "by": added_by,
            "now": _now_iso_seconds(),
        },
    )
    conn.commit()
    return result.rowcount == 1


def remove_team_inviter(
    conn: Connection,
    *,
    guild_id: str,
    user_id: str,
) -> bool:
    """Remove a Discord user-id from the allowlist. Returns True if a
    row was removed.

    Past invites created by this user are NOT retroactively invalidated.
    The next join from such an invite will see ``is_team_inviter == False``
    and get airlocked.
    """
    result = conn.execute(
        text(
            "DELETE FROM discord_team_inviters"
            " WHERE guild_id = :g AND user_id = :u"
        ),
        {"g": guild_id, "u": user_id},
    )
    conn.commit()
    return result.rowcount == 1


def is_team_inviter(
    conn: Connection,
    guild_id: str,
    user_id: str,
) -> bool:
    """True iff (guild_id, user_id) is on the team-inviter allowlist."""
    row = conn.execute(
        text(
            "SELECT 1 FROM discord_team_inviters"
            " WHERE guild_id = :g AND user_id = :u LIMIT 1"
        ),
        {"g": guild_id, "u": user_id},
    ).fetchone()
    return row is not None


def list_team_inviters(conn: Connection, guild_id: str) -> list[dict]:
    """Return all team-inviter allowlist rows for a guild, oldest-first."""
    rows = conn.execute(
        text(
            "SELECT user_id, added_at, added_by FROM discord_team_inviters"
            " WHERE guild_id = :g ORDER BY added_at ASC, id ASC"
        ),
        {"g": guild_id},
    ).fetchall()
    return [
        {
            "user_id": r["user_id"],
            "added_at": r["added_at"],
            "added_by": r["added_by"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Member admit ledger (2.3)
# ---------------------------------------------------------------------------


def record_member_admit(
    conn: Connection,
    *,
    guild_id: str,
    user_id: str,
    attributed_invite_code: str | None,
    attributed_inviter_user_id: str | None,
    is_team_invite: bool,
    airlock_status: str,
    decision_by: str | None = None,
    decision_reason: str | None = None,
) -> int:
    """Insert or replace the admit row for (guild_id, user_id).

    Called from on_member_join. Rejoin overwrites the prior row's
    attribution + status (decision_by / decision_at / decision_reason
    are reset because the fresh join is a new opportunity for triage).

    Returns the admit row id.
    """
    if airlock_status not in VALID_AIRLOCK_STATUSES:
        raise ValueError(
            f"airlock_status must be one of {VALID_AIRLOCK_STATUSES},"
            f" got {airlock_status!r}"
        )
    now = _now_iso_seconds()
    is_admit_decision = airlock_status in (
        "auto_admitted", "admitted", "banned", "kicked", "left_during_airlock"
    )
    decision_at = now if is_admit_decision else None
    row = conn.execute(
        text(
            "INSERT INTO discord_member_admit"
            " (guild_id, user_id, joined_at, attributed_invite_code,"
            "  attributed_inviter_user_id, is_team_invite, airlock_status,"
            "  decision_by, decision_at, decision_reason)"
            " VALUES (:g, :u, :j, :code, :inviter, :team, :status,"
            "         :decision_by, :decision_at, :reason)"
            " ON CONFLICT (guild_id, user_id) DO UPDATE SET"
            "   joined_at = excluded.joined_at,"
            "   attributed_invite_code = excluded.attributed_invite_code,"
            "   attributed_inviter_user_id = excluded.attributed_inviter_user_id,"
            "   is_team_invite = excluded.is_team_invite,"
            "   airlock_status = excluded.airlock_status,"
            "   decision_by = excluded.decision_by,"
            "   decision_at = excluded.decision_at,"
            "   decision_reason = excluded.decision_reason"
            " RETURNING id"
        ),
        {
            "g": guild_id,
            "u": user_id,
            "j": now,
            "code": attributed_invite_code,
            "inviter": attributed_inviter_user_id,
            "team": 1 if is_team_invite else 0,
            "status": airlock_status,
            "decision_by": decision_by,
            "decision_at": decision_at,
            "reason": decision_reason,
        },
    ).fetchone()
    conn.commit()
    return int(row["id"])


def set_airlock_status(
    conn: Connection,
    *,
    guild_id: str,
    user_id: str,
    new_status: str,
    decision_by: str,
    decision_reason: str | None = None,
) -> bool:
    """Transition a held admit row to admitted / banned / kicked /
    left_during_airlock. Used by /admit, /ban, /kick mod commands +
    on_member_remove.

    Returns True iff a row was updated. False means no admit row exists
    (caller should refuse the action with a friendly bounce — "this
    user isn't in airlock").
    """
    if new_status not in VALID_AIRLOCK_STATUSES:
        raise ValueError(
            f"new_status must be one of {VALID_AIRLOCK_STATUSES},"
            f" got {new_status!r}"
        )
    result = conn.execute(
        text(
            "UPDATE discord_member_admit"
            " SET airlock_status = :status,"
            "     decision_by = :by,"
            "     decision_at = :now,"
            "     decision_reason = :reason"
            " WHERE guild_id = :g AND user_id = :u"
        ),
        {
            "status": new_status,
            "by": decision_by,
            "now": _now_iso_seconds(),
            "reason": decision_reason,
            "g": guild_id,
            "u": user_id,
        },
    )
    conn.commit()
    return result.rowcount == 1


def get_admit(
    conn: Connection,
    guild_id: str,
    user_id: str,
) -> dict | None:
    """Return the admit row for (guild_id, user_id), or None."""
    row = conn.execute(
        text(
            "SELECT id, guild_id, user_id, joined_at, attributed_invite_code,"
            "       attributed_inviter_user_id, is_team_invite, airlock_status,"
            "       decision_by, decision_at, decision_reason"
            " FROM discord_member_admit"
            " WHERE guild_id = :g AND user_id = :u LIMIT 1"
        ),
        {"g": guild_id, "u": user_id},
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "guild_id": row["guild_id"],
        "user_id": row["user_id"],
        "joined_at": row["joined_at"],
        "attributed_invite_code": row["attributed_invite_code"],
        "attributed_inviter_user_id": row["attributed_inviter_user_id"],
        "is_team_invite": bool(row["is_team_invite"]),
        "airlock_status": row["airlock_status"],
        "decision_by": row["decision_by"],
        "decision_at": row["decision_at"],
        "decision_reason": row["decision_reason"],
    }


def list_pending_airlock(conn: Connection, guild_id: str) -> list[dict]:
    """Return all admit rows in 'held' state for a guild, oldest-first.

    Used by /airlock-status (when called without a target) and operator
    spot-checks: "who's waiting for triage right now?"
    """
    rows = conn.execute(
        text(
            "SELECT id, user_id, joined_at, attributed_invite_code,"
            "       attributed_inviter_user_id"
            " FROM discord_member_admit"
            " WHERE guild_id = :g AND airlock_status = 'held'"
            " ORDER BY joined_at ASC, id ASC"
        ),
        {"g": guild_id},
    ).fetchall()
    return [
        {
            "id": r["id"],
            "user_id": r["user_id"],
            "joined_at": r["joined_at"],
            "attributed_invite_code": r["attributed_invite_code"],
            "attributed_inviter_user_id": r["attributed_inviter_user_id"],
        }
        for r in rows
    ]


__all__ = [
    "VALID_AIRLOCK_STATUSES",
    "upsert_invite_snapshot",
    "get_invite_snapshot",
    "delete_invite_snapshot",
    "attribute_join",
    "add_team_inviter",
    "remove_team_inviter",
    "is_team_inviter",
    "list_team_inviters",
    "record_member_admit",
    "set_airlock_status",
    "get_admit",
    "list_pending_airlock",
]
