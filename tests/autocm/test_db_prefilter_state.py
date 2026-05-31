"""C3.4a stateful query-helper tests: sable_platform.autocm.db.

The named SQL helpers that back the pre-filter's DB-driven strong-skips. These
prove the SQL layer (flagged-user / recent-reply / team-pre-emption) in isolation
from the filter logic — every helper takes an open SA Connection, creates no
engine, and is dialect-agnostic (Python-computed ISO-Z cutoffs, named binds).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from sable_platform.autocm import db as autocm_db


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _seed(conn, org_id):
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id}
    )
    conn.execute(
        text(
            "INSERT INTO autocm_clients (org_id, display_name, autonomy_state, enabled) "
            "VALUES (:o, 'RM', 'hitl', 1)"
        ),
        {"o": org_id},
    )
    client_id = conn.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = :o"), {"o": org_id}
    ).fetchone()[0]
    conn.execute(
        text(
            "INSERT INTO relay_chats (org_id, platform, chat_id, title) "
            "VALUES (:o, 'telegram', '-100', 'c')"
        ),
        {"o": org_id},
    )
    chat_id = conn.execute(
        text("SELECT id FROM relay_chats WHERE chat_id = '-100'")
    ).fetchone()[0]
    return client_id, chat_id


def _member(conn, name):
    conn.execute(text("INSERT INTO relay_members (display_name) VALUES (:d)"), {"d": name})
    return conn.execute(
        text("SELECT id FROM relay_members WHERE display_name = :d ORDER BY id DESC"),
        {"d": name},
    ).fetchone()[0]


def _msg(conn, org_id, chat_id, member_id, ago):
    received = _now() - ago
    conn.execute(
        text(
            "INSERT INTO relay_messages "
            "(org_id, chat_id, member_id, platform, external_message_id, received_at) "
            "VALUES (:o, :c, :m, 'telegram', :emi, :ra)"
        ),
        {"o": org_id, "c": chat_id, "m": member_id, "emi": f"e{received.timestamp()}", "ra": _iso(received)},
    )


# --- is_flagged_user -------------------------------------------------------
def test_is_flagged_user_member(sa_org):
    conn, org_id = sa_org
    client_id, _ = _seed(conn, org_id)
    m = _member(conn, "x")
    conn.execute(
        text(
            "INSERT INTO autocm_flagged_users (client_id, member_id, status) "
            "VALUES (:c, :m, 'silenced')"
        ),
        {"c": client_id, "m": m},
    )
    conn.commit()
    assert autocm_db.is_flagged_user(conn, client_id, member_id=m) is True
    assert autocm_db.is_flagged_user(conn, client_id, member_id=9999) is False


def test_is_flagged_user_external_id(sa_org):
    conn, org_id = sa_org
    client_id, _ = _seed(conn, org_id)
    conn.execute(
        text(
            "INSERT INTO autocm_flagged_users (client_id, external_user_id, status) "
            "VALUES (:c, 'tg:7', 'silenced')"
        ),
        {"c": client_id},
    )
    conn.commit()
    assert autocm_db.is_flagged_user(conn, client_id, external_user_id="tg:7") is True
    assert autocm_db.is_flagged_user(conn, client_id, external_user_id="tg:8") is False


def test_is_flagged_user_cleared_is_false(sa_org):
    conn, org_id = sa_org
    client_id, _ = _seed(conn, org_id)
    m = _member(conn, "y")
    conn.execute(
        text(
            "INSERT INTO autocm_flagged_users (client_id, member_id, status) "
            "VALUES (:c, :m, 'cleared')"
        ),
        {"c": client_id, "m": m},
    )
    conn.commit()
    assert autocm_db.is_flagged_user(conn, client_id, member_id=m) is False


def test_is_flagged_user_no_identity_is_false(sa_org):
    conn, org_id = sa_org
    client_id, _ = _seed(conn, org_id)
    conn.commit()
    assert autocm_db.is_flagged_user(conn, client_id) is False


def test_is_flagged_user_scoped_to_client(sa_org):
    conn, org_id = sa_org
    client_id, _ = _seed(conn, org_id)
    m = _member(conn, "z")
    # a SECOND real client (FK-valid) flags the member; the first client must not
    # see that flag — the silence is per-client.
    conn.execute(
        text("INSERT INTO orgs (org_id, display_name) VALUES ('org_two', 'Two')")
    )
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled) VALUES ('org_two', 1)")
    )
    conn.execute(
        text(
            "INSERT INTO autocm_clients (org_id, display_name, autonomy_state, enabled) "
            "VALUES ('org_two', 'Two', 'hitl', 1)"
        )
    )
    other_client = conn.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = 'org_two'")
    ).fetchone()[0]
    conn.execute(
        text(
            "INSERT INTO autocm_flagged_users (client_id, member_id, status) "
            "VALUES (:c, :m, 'silenced')"
        ),
        {"c": other_client, "m": m},
    )
    conn.commit()
    assert autocm_db.is_flagged_user(conn, client_id, member_id=m) is False
    assert autocm_db.is_flagged_user(conn, other_client, member_id=m) is True


# --- member_replied_within -------------------------------------------------
def test_member_replied_within_true(sa_org):
    conn, org_id = sa_org
    client_id, chat_id = _seed(conn, org_id)
    other = _member(conn, "other")
    _msg(conn, org_id, chat_id, other, timedelta(seconds=30))
    conn.commit()
    assert autocm_db.member_replied_within(conn, chat_id, seconds=60) is True


def test_member_replied_within_excludes_self(sa_org):
    conn, org_id = sa_org
    client_id, chat_id = _seed(conn, org_id)
    me = _member(conn, "me")
    _msg(conn, org_id, chat_id, me, timedelta(seconds=10))
    conn.commit()
    assert (
        autocm_db.member_replied_within(conn, chat_id, seconds=60, exclude_member_id=me)
        is False
    )


def test_member_replied_within_outside_window_false(sa_org):
    conn, org_id = sa_org
    client_id, chat_id = _seed(conn, org_id)
    other = _member(conn, "other2")
    _msg(conn, org_id, chat_id, other, timedelta(seconds=120))
    conn.commit()
    assert autocm_db.member_replied_within(conn, chat_id, seconds=60) is False


# --- team_posted_within ----------------------------------------------------
def test_team_posted_within_client_team_true(sa_org):
    conn, org_id = sa_org
    client_id, chat_id = _seed(conn, org_id)
    founder = _member(conn, "founder")
    conn.execute(
        text(
            "INSERT INTO relay_member_roles (member_id, org_id, role) "
            "VALUES (:m, :o, 'client_team')"
        ),
        {"m": founder, "o": org_id},
    )
    _msg(conn, org_id, chat_id, founder, timedelta(minutes=2))
    conn.commit()
    assert autocm_db.team_posted_within(conn, org_id, chat_id, minutes=5) is True


def test_team_posted_within_sable_operator_excluded(sa_org):
    conn, org_id = sa_org
    client_id, chat_id = _seed(conn, org_id)
    op = _member(conn, "op")
    conn.execute(
        text(
            "INSERT INTO relay_member_roles (member_id, org_id, role) "
            "VALUES (:m, :o, 'sable_operator')"
        ),
        {"m": op, "o": org_id},
    )
    _msg(conn, org_id, chat_id, op, timedelta(minutes=2))
    conn.commit()
    assert autocm_db.team_posted_within(conn, org_id, chat_id, minutes=5) is False


def test_team_posted_within_excludes_self_member(sa_org):
    conn, org_id = sa_org
    client_id, chat_id = _seed(conn, org_id)
    founder = _member(conn, "founder_self")
    conn.execute(
        text(
            "INSERT INTO relay_member_roles (member_id, org_id, role) "
            "VALUES (:m, :o, 'client_team')"
        ),
        {"m": founder, "o": org_id},
    )
    # the ONLY in-window team post is the founder's own → excluded → no pre-emption
    _msg(conn, org_id, chat_id, founder, timedelta(minutes=2))
    conn.commit()
    assert (
        autocm_db.team_posted_within(
            conn, org_id, chat_id, minutes=5, exclude_member_id=founder
        )
        is False
    )
    # without the exclusion it DOES pre-empt (proves the post is genuinely in-window)
    assert autocm_db.team_posted_within(conn, org_id, chat_id, minutes=5) is True


def test_team_posted_within_self_exclusion_does_not_hide_other_team(sa_org):
    conn, org_id = sa_org
    client_id, chat_id = _seed(conn, org_id)
    asker = _member(conn, "asker_team")
    other = _member(conn, "other_team")
    for m in (asker, other):
        conn.execute(
            text(
                "INSERT INTO relay_member_roles (member_id, org_id, role) "
                "VALUES (:m, :o, 'client_team')"
            ),
            {"m": m, "o": org_id},
        )
    _msg(conn, org_id, chat_id, asker, timedelta(minutes=2))
    _msg(conn, org_id, chat_id, other, timedelta(minutes=2))
    conn.commit()
    # excluding the asker still leaves the OTHER team member's post → pre-emption
    assert (
        autocm_db.team_posted_within(
            conn, org_id, chat_id, minutes=5, exclude_member_id=asker
        )
        is True
    )


def test_team_posted_within_excludes_self_external_id(sa_org):
    conn, org_id = sa_org
    client_id, chat_id = _seed(conn, org_id)
    founder = _member(conn, "founder_ext")
    conn.execute(
        text(
            "INSERT INTO relay_member_roles (member_id, org_id, role) "
            "VALUES (:m, :o, 'admin')"
        ),
        {"m": founder, "o": org_id},
    )
    received = _now() - timedelta(minutes=2)
    conn.execute(
        text(
            "INSERT INTO relay_messages "
            "(org_id, chat_id, member_id, platform, external_message_id, "
            " external_user_id, received_at) "
            "VALUES (:o, :c, :m, 'telegram', :emi, 'tg:42', :ra)"
        ),
        {
            "o": org_id,
            "c": chat_id,
            "m": founder,
            "emi": f"e{received.timestamp()}",
            "ra": _iso(received),
        },
    )
    conn.commit()
    assert (
        autocm_db.team_posted_within(
            conn, org_id, chat_id, minutes=5, exclude_external_user_id="tg:42"
        )
        is False
    )


def test_team_posted_within_outside_window_false(sa_org):
    conn, org_id = sa_org
    client_id, chat_id = _seed(conn, org_id)
    admin = _member(conn, "admin")
    conn.execute(
        text(
            "INSERT INTO relay_member_roles (member_id, org_id, role) "
            "VALUES (:m, :o, 'admin')"
        ),
        {"m": admin, "o": org_id},
    )
    _msg(conn, org_id, chat_id, admin, timedelta(minutes=10))
    conn.commit()
    assert autocm_db.team_posted_within(conn, org_id, chat_id, minutes=5) is False
