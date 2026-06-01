"""Reply-ping preferences + ``/whoami`` (C2.3b).

PLAN §4 commands (the member-facing, DM-with-bot surface):

  * ``/optin-replies [client]``  — subscribe to reply pings for a client.
  * ``/optout-replies [client]`` — unsubscribe.
  * ``/mute-replies <duration>`` — mute reply pings for N hours/days.
  * ``/whoami``                  — show your role + opt-in state (any chat).

All keyed on ``(member_id, org_id)`` in ``relay_member_preferences`` (the 057
PK). The member is auto-created on first interaction
(:func:`relay_db.auto_create_member_identity`); **auto-creation grants NO role**
(§8) — preferences are independent of roles, so any member (even unregistered)
may opt in/out and run ``/whoami``. Per the LOCKED C2.1 §5.3 layering boundary
this module embeds NO raw SQL (every statement is a named ``relay/db.py``
helper) and runs all writes inside ONE ``immediate_txn`` with NO external send
inside it (the §3.1 / C2.2 invariant); it returns a small result object the
listener uses to reply OUTSIDE the txn.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.engine import Connection

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.txn import immediate_txn

logger = logging.getLogger(__name__)


# Machine-stable outcome codes (asserted by tests / used for the listener reply).
PREF_OPTED_IN = "opted_in"
PREF_OPTED_OUT = "opted_out"
PREF_MUTED = "muted"
PREF_BAD_DURATION = "bad_duration"  # /mute-replies could not parse the duration


# ``<N><unit>`` where unit ∈ {m, h, d} (minutes/hours/days); a bare integer is
# treated as hours (the PLAN §4 "N hours/days" default unit).
_DURATION_RE = re.compile(r"^\s*(\d+)\s*([mhd]?)\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "": 3600}


@dataclass(frozen=True)
class PreferenceResult:
    """Outcome of a preference command (drives the OUTSIDE-the-txn DM reply).

    ``code`` is one of the ``PREF_*`` constants. ``optin`` is the resulting opt-in
    state; ``mute_until`` is the resulting ISO-Z mute expiry (or ``None`` when not
    muted). ``member_id`` is the resolved (auto-created) member.
    """

    code: str
    member_id: int | None = None
    org_id: str | None = None
    optin: bool = False
    mute_until: str | None = None


@dataclass(frozen=True)
class WhoamiResult:
    """The ``/whoami`` view (role + opt-in state, §4).

    ``roles`` are the relay roles the member holds for ``org_id`` (empty for an
    unregistered member — auto-creation grants none, §8). ``optin`` / ``mute_until``
    reflect the member's reply-ping preference. ``handle`` is the display handle
    last seen on the member's identity (display-only, §15.4).
    """

    member_id: int
    org_id: str | None
    handle: str | None
    roles: tuple
    optin: bool
    mute_until: str | None


def _parse_duration_seconds(arg: str) -> int | None:
    """Parse a ``/mute-replies`` duration → seconds, or ``None`` if unparseable.

    Accepts ``30m`` / ``2h`` / ``3d`` / a bare ``12`` (hours). Returns ``None`` for
    a missing/garbled/zero/negative duration so the handler reports
    ``PREF_BAD_DURATION`` rather than silently muting forever or not at all.
    """
    if not arg:
        return None
    m = _DURATION_RE.match(arg)
    if m is None:
        return None
    qty = int(m.group(1))
    if qty <= 0:
        return None
    unit = m.group(2).lower()
    return qty * _UNIT_SECONDS[unit]


def optin_replies(
    conn: Connection,
    *,
    org_id: str,
    platform: str,
    external_user_id: str,
    handle: str | None = None,
) -> PreferenceResult:
    """``/optin-replies`` — subscribe the member to reply pings for an org.

    Auto-creates the member identity (audit only — no role) and sets
    ``replies_optin = 1`` for ``(member_id, org_id)``. Opting in does NOT clear an
    existing mute (a member can be opted-in but muted). Idempotent.
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(f"unknown relay platform {platform!r}")
    with immediate_txn(conn):
        member_id = relay_db.auto_create_member_identity(
            conn, platform, str(external_user_id), handle=handle
        )
        relay_db.upsert_member_preference(conn, member_id, org_id, replies_optin=True)
        pref = relay_db.get_member_preference(conn, member_id, org_id)
    return PreferenceResult(
        code=PREF_OPTED_IN,
        member_id=member_id,
        org_id=org_id,
        optin=True,
        mute_until=pref.get("mute_until") if pref else None,
    )


def optout_replies(
    conn: Connection,
    *,
    org_id: str,
    platform: str,
    external_user_id: str,
    handle: str | None = None,
) -> PreferenceResult:
    """``/optout-replies`` — unsubscribe the member from reply pings for an org.

    Sets ``replies_optin = 0`` (the member is removed from the Flow D default
    fan-out set). Idempotent. Does not touch the mute (opt-out subsumes it for
    fan-out purposes, but the stored value is left untouched for audit clarity).
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(f"unknown relay platform {platform!r}")
    with immediate_txn(conn):
        member_id = relay_db.auto_create_member_identity(
            conn, platform, str(external_user_id), handle=handle
        )
        relay_db.upsert_member_preference(conn, member_id, org_id, replies_optin=False)
        pref = relay_db.get_member_preference(conn, member_id, org_id)
    return PreferenceResult(
        code=PREF_OPTED_OUT,
        member_id=member_id,
        org_id=org_id,
        optin=False,
        mute_until=pref.get("mute_until") if pref else None,
    )


def mute_replies(
    conn: Connection,
    *,
    org_id: str,
    platform: str,
    external_user_id: str,
    duration: str,
    handle: str | None = None,
    now: datetime | None = None,
) -> PreferenceResult:
    """``/mute-replies <duration>`` — mute reply pings for N minutes/hours/days.

    Parses ``duration`` (``30m`` / ``2h`` / ``3d`` / bare ``N`` = hours); an
    unparseable/zero duration returns ``PREF_BAD_DURATION`` and writes NOTHING.
    On success, materializes ``mute_until = now + duration`` (computed in Python,
    bound as a param — dialect-agnostic) on ``relay_member_preferences``. Muting
    leaves ``replies_optin`` untouched (a muted opted-in member is suppressed by
    the §11 #1 fan-out query's ``mute_until`` predicate until expiry).
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(f"unknown relay platform {platform!r}")
    seconds = _parse_duration_seconds(duration)
    if seconds is None:
        return PreferenceResult(code=PREF_BAD_DURATION, org_id=org_id)
    base = now or datetime.now(timezone.utc)
    mute_until = (base + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with immediate_txn(conn):
        member_id = relay_db.auto_create_member_identity(
            conn, platform, str(external_user_id), handle=handle
        )
        relay_db.upsert_member_preference(
            conn, member_id, org_id, mute_until=mute_until
        )
        pref = relay_db.get_member_preference(conn, member_id, org_id)
    return PreferenceResult(
        code=PREF_MUTED,
        member_id=member_id,
        org_id=org_id,
        optin=bool(pref.get("replies_optin")) if pref else False,
        mute_until=mute_until,
    )


def whoami(
    conn: Connection,
    *,
    org_id: str | None,
    platform: str,
    external_user_id: str,
    handle: str | None = None,
) -> WhoamiResult:
    """``/whoami`` — show the caller's role + opt-in state (§4).

    Auto-creates the member identity (this is the §8 mode-2 "self-claim" entry
    point: DMing ``/whoami`` populates ``relay_member_identities`` so an admin can
    later resolve the handle). When ``org_id`` is provided, returns the roles the
    member holds for that org and their reply-ping preference; with ``org_id=None``
    (a chat not bound to a client) roles/preferences are empty. Auto-creation
    grants NO role.
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(f"unknown relay platform {platform!r}")
    with immediate_txn(conn):
        member_id = relay_db.auto_create_member_identity(
            conn, platform, str(external_user_id), handle=handle
        )
        roles: list[str] = []
        optin = False
        mute_until: str | None = None
        if org_id is not None:
            roles = relay_db.list_member_roles(conn, member_id, org_id)
            pref = relay_db.get_member_preference(conn, member_id, org_id)
            if pref is not None:
                optin = bool(pref.get("replies_optin"))
                mute_until = pref.get("mute_until")
        display = relay_db.get_identity_handle(conn, member_id, platform) or handle
    return WhoamiResult(
        member_id=member_id,
        org_id=org_id,
        handle=display,
        roles=tuple(roles),
        optin=optin,
        mute_until=mute_until,
    )


__all__ = [
    "PreferenceResult",
    "WhoamiResult",
    "optin_replies",
    "optout_replies",
    "mute_replies",
    "whoami",
    "PREF_OPTED_IN",
    "PREF_OPTED_OUT",
    "PREF_MUTED",
    "PREF_BAD_DURATION",
]
