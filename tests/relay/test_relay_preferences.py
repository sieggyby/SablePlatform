"""C2.3b tests — reply-ping preferences (/optin /optout /mute) + /whoami.

No real Telegram/Discord; DB work runs against the in-memory ``sa_conn`` schema.
Each command returns a small result object the listener uses to reply OUTSIDE the
txn.

Coverage (per MEGAPLAN C2.3b exit):
  * opt-in / opt-out round-trips persist ``replies_optin`` on
    ``relay_member_preferences``.
  * an auto-created identity (first interaction) grants NO role.
  * ``/mute-replies <duration>`` materializes ``mute_until`` (and rejects a garbled
    duration); a muted opted-in member is suppressed from the Flow D fan-out.
  * ``/whoami`` reports the caller's role + opt-in state and self-claims the
    identity.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.handlers import preferences


def _seed(conn, *, org_id="orgA"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled, config) VALUES (:o, 1, '{}')"),
        {"o": org_id},
    )


def _pref(conn, member_id, org_id):
    return relay_db.get_member_preference(conn, member_id, org_id)


# ==========================================================================
def test_optin_optout_round_trip(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()

    r1 = preferences.optin_replies(
        sa_conn, org_id="orgA", platform="telegram", external_user_id="20", handle="alice"
    )
    assert r1.code == preferences.PREF_OPTED_IN
    assert r1.optin is True
    assert _pref(sa_conn, r1.member_id, "orgA")["replies_optin"] == 1

    r2 = preferences.optout_replies(
        sa_conn, org_id="orgA", platform="telegram", external_user_id="20", handle="alice"
    )
    assert r2.code == preferences.PREF_OPTED_OUT
    assert r2.optin is False
    assert r2.member_id == r1.member_id  # same member
    assert _pref(sa_conn, r1.member_id, "orgA")["replies_optin"] == 0


def test_optin_is_idempotent(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()

    a = preferences.optin_replies(
        sa_conn, org_id="orgA", platform="telegram", external_user_id="20", handle="alice"
    )
    b = preferences.optin_replies(
        sa_conn, org_id="orgA", platform="telegram", external_user_id="20", handle="alice"
    )
    assert a.member_id == b.member_id
    n = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_member_preferences WHERE member_id = :m"),
        {"m": a.member_id},
    ).fetchone()[0]
    assert n == 1


def test_auto_created_identity_grants_no_role(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()

    r = preferences.optin_replies(
        sa_conn, org_id="orgA", platform="telegram", external_user_id="20", handle="alice"
    )
    # Opting in created the identity but granted NO role (§8).
    assert relay_db.list_member_roles(sa_conn, r.member_id, "orgA") == []
    assert relay_db.is_relay_operator(sa_conn, r.member_id, "orgA") is False


def test_mute_replies_sets_mute_until(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()

    now = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    r = preferences.mute_replies(
        sa_conn, org_id="orgA", platform="telegram", external_user_id="20",
        duration="2h", handle="alice", now=now,
    )
    assert r.code == preferences.PREF_MUTED
    assert r.mute_until == "2026-05-31T14:00:00Z"
    assert _pref(sa_conn, r.member_id, "orgA")["mute_until"] == "2026-05-31T14:00:00Z"


def test_mute_replies_day_unit(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    now = datetime(2026, 5, 31, 0, 0, 0, tzinfo=timezone.utc)
    r = preferences.mute_replies(
        sa_conn, org_id="orgA", platform="telegram", external_user_id="20",
        duration="3d", now=now,
    )
    assert r.mute_until == "2026-06-03T00:00:00Z"


def test_mute_replies_bad_duration_writes_nothing(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    r = preferences.mute_replies(
        sa_conn, org_id="orgA", platform="telegram", external_user_id="20",
        duration="soon", handle="alice",
    )
    assert r.code == preferences.PREF_BAD_DURATION
    # No preference row was written (nothing to clean up).
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_member_preferences")
    ).fetchone()[0] == 0


def test_mute_replies_zero_rejected(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    r = preferences.mute_replies(
        sa_conn, org_id="orgA", platform="telegram", external_user_id="20", duration="0h",
    )
    assert r.code == preferences.PREF_BAD_DURATION


def test_muted_optedin_member_excluded_from_fanout(sa_conn):
    """A muted opted-in member is suppressed by the §11 #1 fan-out query."""
    _seed(sa_conn)
    sa_conn.commit()

    r = preferences.optin_replies(
        sa_conn, org_id="orgA", platform="telegram", external_user_id="20", handle="alice"
    )
    now = datetime.now(timezone.utc)
    preferences.mute_replies(
        sa_conn, org_id="orgA", platform="telegram", external_user_id="20",
        duration="5d", now=now,
    )
    fanout = relay_db.list_optedin_members(sa_conn, "orgA")
    assert all(m["member_id"] != r.member_id for m in fanout)


def test_whoami_reports_role_and_optin(sa_conn):
    _seed(sa_conn)
    # Grant an operator role to user 20.
    mid = relay_db.auto_create_member_identity(sa_conn, "telegram", "20", handle="alice")
    sa_conn.execute(
        text("INSERT INTO relay_member_roles (member_id, org_id, role) VALUES (:m, :o, 'sable_operator')"),
        {"m": mid, "o": "orgA"},
    )
    sa_conn.commit()

    preferences.optin_replies(
        sa_conn, org_id="orgA", platform="telegram", external_user_id="20", handle="alice"
    )
    who = preferences.whoami(
        sa_conn, org_id="orgA", platform="telegram", external_user_id="20", handle="alice"
    )
    assert who.member_id == mid
    assert "sable_operator" in who.roles
    assert who.optin is True
    assert who.handle == "alice"


def test_whoami_self_claims_identity(sa_conn):
    """DMing /whoami populates relay_member_identities (the §8 mode-2 entry)."""
    _seed(sa_conn)
    sa_conn.commit()

    assert relay_db.resolve_member_id(sa_conn, "telegram", "77") is None
    who = preferences.whoami(
        sa_conn, org_id="orgA", platform="telegram", external_user_id="77", handle="newbie"
    )
    # Identity now exists, with no roles.
    assert relay_db.resolve_member_id(sa_conn, "telegram", "77") == who.member_id
    assert who.roles == ()
    assert who.optin is False


def test_whoami_no_org_empty_roles(sa_conn):
    """/whoami in a chat not bound to a client returns empty roles/prefs."""
    _seed(sa_conn)
    sa_conn.commit()
    who = preferences.whoami(
        sa_conn, org_id=None, platform="telegram", external_user_id="20", handle="alice"
    )
    assert who.roles == ()
    assert who.optin is False
    assert who.org_id is None
