"""AutoCM stateful query helpers (MEGAPLAN C3.4a — the runtime-state SQL layer).

The C3.4a stateful pre-filter (``classifier/filter.prefilter``) consults AutoCM +
relay RUNTIME STATE the dependency-light vendored core (pyyaml+httpx only, no
SP/relay dep) cannot read. All of that SQL lives HERE, behind named, typed
functions — the same layering boundary ``sable_platform.relay.db`` and
``sable_platform.autocm.loaders`` enforce: every helper takes an already-open
SQLAlchemy ``Connection`` (the caller owns lifecycle), this module creates NO
engine, and the classifier never embeds raw SQL.

The three DB-backed strong-skips (CLASSIFIER §1 / LATENCY §2):

  * :func:`is_flagged_user`       — author currently auto-silenced
                                    (``autocm_flagged_users.status = 'silenced'``).
  * :func:`member_replied_within` — another community member already replied in the
                                    same chat within N seconds (NULO doesn't pile on).
  * :func:`team_posted_within`    — the founder / any tier-2 client-team member
                                    posted in the same chat within N minutes
                                    (founder pre-emption lookback).

All timestamps are computed in Python as UTC ISO-8601 ``...Z`` and bound as
parameters (never ``strftime('now')``), so the SQL is dialect-agnostic and runs
unchanged on the live Postgres pool — matching the relay/db.py contract.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

# The relay member roles that mark a CLIENT-SIDE principal (founder / their team)
# whose own post in a thread pre-empts a NULO reply. Per the 057 CHECK these are
# the client's people (``client_team``) and chat admins (``admin``). The
# ``sable_operator`` role is Sable's own human handler of the bot — NOT a
# community member whose post should suppress the bot — so it is deliberately
# excluded from the pre-emption set.
TEAM_PREEMPTION_ROLES = ("client_team", "admin")


def _utc_now_iso() -> str:
    """UTC ISO-8601 ``...Z`` timestamp matching the relay/autocm TEXT columns."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_seconds_ago(seconds: int, *, now: Optional[datetime] = None) -> str:
    """ISO-8601 ``...Z`` cutoff ``seconds`` before ``now`` (UTC), bound as a param.

    ``now`` is injectable so tests can pin the clock deterministically without
    sleeping; production callers pass ``None`` to use the real wall clock.
    """
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (base - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_flagged_user(
    conn: Connection,
    client_id: int,
    *,
    member_id: Optional[int] = None,
    external_user_id: Optional[str] = None,
) -> bool:
    """True iff the author is currently auto-silenced for this client.

    Matches on EITHER the resolved relay ``member_id`` OR the raw
    ``external_user_id`` (an unlinked author still gets silenced by external id),
    scoped to ``client_id`` and ``status = 'silenced'`` (a ``cleared`` row no
    longer suppresses). Returns ``False`` when neither identifier is supplied — a
    pre-filter that cannot identify the author must not silently drop on the
    flagged-user rule (the other rules still apply).
    """
    if member_id is None and external_user_id is None:
        return False

    clauses = []
    params: dict = {"client_id": client_id}
    if member_id is not None:
        clauses.append("member_id = :member_id")
        params["member_id"] = member_id
    if external_user_id is not None:
        clauses.append("external_user_id = :external_user_id")
        params["external_user_id"] = external_user_id
    identity_clause = " OR ".join(clauses)

    row = conn.execute(
        text(
            "SELECT 1 FROM autocm_flagged_users "
            "WHERE client_id = :client_id AND status = 'silenced' "
            f"  AND ({identity_clause}) "
            "LIMIT 1"
        ),
        params,
    ).fetchone()
    return row is not None


def member_replied_within(
    conn: Connection,
    chat_row_id: int,
    *,
    seconds: int = 60,
    exclude_member_id: Optional[int] = None,
    exclude_external_user_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> bool:
    """True iff ANOTHER member posted in this chat within the last ``seconds``.

    The "don't pile on" strong-skip (CLASSIFIER §1 / VOICE §3): if a fellow
    community member has already engaged a thread within the window, NULO stays
    quiet. ``chat_row_id`` is ``relay_chats.id`` (the corpus surface). The
    current author is excluded so their OWN message in-window does not count as a
    reply (by ``member_id`` when linked, else by ``external_user_id``).

    ``relay_messages`` carries no first-class thread id, so the window is scoped
    to the chat over the time window — the spec's "another community member
    already replied within 60 seconds" is a recency-in-chat signal, matching the
    LATENCY §2 cheap-membership-test intent (no thread reconstruction in v1).
    """
    cutoff = _iso_seconds_ago(seconds, now=now)
    params: dict = {"chat_id": chat_row_id, "cutoff": cutoff}
    exclude_sql = ""
    if exclude_member_id is not None:
        exclude_sql += " AND (member_id IS NULL OR member_id <> :ex_member)"
        params["ex_member"] = exclude_member_id
    if exclude_external_user_id is not None:
        exclude_sql += (
            " AND (external_user_id IS NULL OR external_user_id <> :ex_ext)"
        )
        params["ex_ext"] = exclude_external_user_id

    row = conn.execute(
        text(
            "SELECT 1 FROM relay_messages "
            "WHERE chat_id = :chat_id AND received_at >= :cutoff "
            f"{exclude_sql} "
            "LIMIT 1"
        ),
        params,
    ).fetchone()
    return row is not None


def team_posted_within(
    conn: Connection,
    org_id: str,
    chat_row_id: int,
    *,
    minutes: int = 5,
    exclude_member_id: Optional[int] = None,
    exclude_external_user_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> bool:
    """True iff the founder / a tier-2 client-team member posted here recently.

    Founder pre-emption (CLASSIFIER §1 / LATENCY §2): if the client's own people
    (``client_team`` / ``admin`` per :data:`TEAM_PREEMPTION_ROLES`) have spoken in
    the chat within ``minutes``, the bot defers to them and stays silent.

    Joins ``relay_messages`` to ``relay_member_roles`` on the message author's
    ``member_id`` for the org, restricted to the pre-emption roles and the time
    window. ``chat_row_id`` is ``relay_chats.id``; ``org_id`` scopes the role
    grants (roles are per-org).

    The CURRENT author is excluded (by ``member_id`` when linked, else by
    ``external_user_id``), mirroring :func:`member_replied_within`: a founder /
    admin who is THEMSELVES the asker must not pre-empt — i.e. suppress — a reply
    to their OWN just-sent message. Without this, if the relay persists the
    incoming message before the pre-filter runs, rule (d) would match the
    principal's own post and silence the bot on exactly the person it should be
    most responsive to.
    """
    cutoff = _iso_seconds_ago(minutes * 60, now=now)
    placeholders = ", ".join(
        f":role_{i}" for i in range(len(TEAM_PREEMPTION_ROLES))
    )
    params: dict = {"chat_id": chat_row_id, "org_id": org_id, "cutoff": cutoff}
    for i, role in enumerate(TEAM_PREEMPTION_ROLES):
        params[f"role_{i}"] = role

    exclude_sql = ""
    if exclude_member_id is not None:
        exclude_sql += " AND (m.member_id IS NULL OR m.member_id <> :ex_member)"
        params["ex_member"] = exclude_member_id
    if exclude_external_user_id is not None:
        exclude_sql += (
            " AND (m.external_user_id IS NULL OR m.external_user_id <> :ex_ext)"
        )
        params["ex_ext"] = exclude_external_user_id

    row = conn.execute(
        text(
            "SELECT 1 FROM relay_messages m "
            "JOIN relay_member_roles r "
            "  ON r.member_id = m.member_id AND r.org_id = :org_id "
            "WHERE m.chat_id = :chat_id AND m.received_at >= :cutoff "
            f"  AND r.role IN ({placeholders}) "
            f"{exclude_sql} "
            "LIMIT 1"
        ),
        params,
    ).fetchone()
    return row is not None


__all__ = [
    "TEAM_PREEMPTION_ROLES",
    "is_flagged_user",
    "member_replied_within",
    "team_posted_within",
]
