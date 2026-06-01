"""C2.5 db-helper tests for the relay operator-CLI surface.

Exercises the new ``relay/db.py`` helpers that back ``cli/relay_cmds.py``
directly against the in-memory ``sa_conn`` fixture (full schema incl. the 057
relay_* tables). The kill-switch (``kill_org_inflight_jobs``) is the load-bearing
one: it must flip exactly the §3.1 in-flight states (pending/retry/claimed) to
``dead`` and never invent a ``halted`` state.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from sable_platform.relay import db as relay_db


def _seed_org(conn, org_id: str) -> None:
    conn.execute(
        text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"),
        {"o": org_id},
    )


def _seed_tweet(conn, x_id: str, handle: str = "alice") -> int:
    conn.execute(
        text(
            "INSERT INTO relay_tweets (x_id, x_author_handle, text) "
            "VALUES (:x, :h, 'hi')"
        ),
        {"x": x_id, "h": handle},
    )
    return conn.execute(
        text("SELECT id FROM relay_tweets WHERE x_id = :x"), {"x": x_id}
    ).fetchone()[0]


def _seed_member(conn, name: str) -> int:
    conn.execute(
        text("INSERT INTO relay_members (display_name) VALUES (:n)"), {"n": name}
    )
    return conn.execute(
        text("SELECT id FROM relay_members WHERE display_name = :n"), {"n": name}
    ).fetchone()[0]


def _seed_submission(conn, org_id: str, tweet_id: int, member_id: int, status: str) -> int:
    conn.execute(
        text(
            "INSERT INTO relay_submissions "
            "(org_id, tweet_id, submitter_id, source_chat_id, source_message_id, "
            " source_role, status, expires_at) "
            "VALUES (:o, :t, :m, 'c1', 'm1', 'operator', :s, '2099-01-01T00:00:00Z')"
        ),
        {"o": org_id, "t": tweet_id, "m": member_id, "s": status},
    )
    return conn.execute(
        text("SELECT id FROM relay_submissions WHERE org_id = :o ORDER BY id DESC LIMIT 1"),
        {"o": org_id},
    ).fetchone()[0]


def _seed_job(conn, org_id: str, tweet_id: int, state: str, chat: str = "d1") -> int:
    conn.execute(
        text(
            "INSERT INTO relay_publication_jobs "
            "(org_id, tweet_id, destination_platform, destination_chat_id, state) "
            "VALUES (:o, :t, 'discord', :c, :s)"
        ),
        {"o": org_id, "t": tweet_id, "c": chat, "s": state},
    )
    return conn.execute(
        text("SELECT id FROM relay_publication_jobs ORDER BY id DESC LIMIT 1")
    ).fetchone()[0]


# ------------------------------------------------------------------
# ensure_relay_client / set_relay_client_enabled
# ------------------------------------------------------------------
def test_ensure_relay_client_creates_then_idempotent(sa_conn):
    _seed_org(sa_conn, "acme")
    assert relay_db.ensure_relay_client(sa_conn, "acme", enabled=0) is True
    # idempotent — second call returns False, no second row
    assert relay_db.ensure_relay_client(sa_conn, "acme", enabled=0) is False
    client = relay_db.get_relay_client(sa_conn, "acme")
    assert client is not None and client["enabled"] == 0


def test_set_relay_client_enabled_toggles(sa_conn):
    _seed_org(sa_conn, "acme")
    relay_db.ensure_relay_client(sa_conn, "acme", enabled=0)
    relay_db.set_relay_client_enabled(sa_conn, "acme", enabled=1)
    assert relay_db.get_relay_client(sa_conn, "acme")["enabled"] == 1
    assert relay_db.list_enabled_clients(sa_conn) == ["acme"]
    relay_db.set_relay_client_enabled(sa_conn, "acme", enabled=0)
    assert relay_db.list_enabled_clients(sa_conn) == []


# ------------------------------------------------------------------
# kill_org_inflight_jobs — the kill-switch fan-out
# ------------------------------------------------------------------
def test_kill_org_inflight_jobs_flips_only_inflight_states(sa_conn):
    _seed_org(sa_conn, "acme")
    relay_db.ensure_relay_client(sa_conn, "acme", enabled=1)
    t = _seed_tweet(sa_conn, "111")
    j_pending = _seed_job(sa_conn, "acme", t, "pending", chat="d1")
    j_retry = _seed_job(sa_conn, "acme", t, "retry", chat="d2")
    j_claimed = _seed_job(sa_conn, "acme", t, "claimed", chat="d3")
    j_done = _seed_job(sa_conn, "acme", t, "done", chat="d4")
    j_dead = _seed_job(sa_conn, "acme", t, "dead", chat="d5")

    killed = relay_db.kill_org_inflight_jobs(sa_conn, "acme")
    assert killed == 3  # pending + retry + claimed only

    def _row(jid):
        return sa_conn.execute(
            text("SELECT state, last_error FROM relay_publication_jobs WHERE id = :i"),
            {"i": jid},
        ).fetchone()

    for jid in (j_pending, j_retry, j_claimed):
        state, last_error = _row(jid)
        assert state == "dead"
        assert last_error == "org disabled by operator"
    # terminal jobs untouched
    assert _row(j_done)[0] == "done"
    assert _row(j_dead)[0] == "dead"
    assert _row(j_dead)[1] is None  # not re-stamped


def test_kill_org_inflight_jobs_is_org_scoped(sa_conn):
    """Only the targeted org's jobs are killed — a second org is untouched."""
    _seed_org(sa_conn, "acme")
    _seed_org(sa_conn, "beta")
    relay_db.ensure_relay_client(sa_conn, "acme", enabled=1)
    relay_db.ensure_relay_client(sa_conn, "beta", enabled=1)
    ta = _seed_tweet(sa_conn, "a1", "ah")
    tb = _seed_tweet(sa_conn, "b1", "bh")
    _seed_job(sa_conn, "acme", ta, "pending", chat="da")
    j_beta = _seed_job(sa_conn, "beta", tb, "pending", chat="db")

    killed = relay_db.kill_org_inflight_jobs(sa_conn, "acme")
    assert killed == 1
    beta_state = sa_conn.execute(
        text("SELECT state FROM relay_publication_jobs WHERE id = :i"), {"i": j_beta}
    ).fetchone()[0]
    assert beta_state == "pending"


def test_kill_switch_only_uses_check_allowed_dead_state(sa_conn):
    """The kill-switch writes 'dead' — a CHECK-allowed §3.1 state, not 'halted'.

    Asserting the write succeeds against the live CHECK constraint proves no
    out-of-set state was invented (a 'halted' write would raise).
    """
    _seed_org(sa_conn, "acme")
    relay_db.ensure_relay_client(sa_conn, "acme", enabled=1)
    t = _seed_tweet(sa_conn, "222")
    _seed_job(sa_conn, "acme", t, "pending")
    relay_db.kill_org_inflight_jobs(sa_conn, "acme")  # must not raise
    states = {
        r[0]
        for r in sa_conn.execute(
            text("SELECT DISTINCT state FROM relay_publication_jobs WHERE org_id = 'acme'")
        ).fetchall()
    }
    assert states == {"dead"}


# ------------------------------------------------------------------
# list_pending_submissions / get_relay_status
# ------------------------------------------------------------------
def test_list_pending_submissions_returns_open_only(sa_conn):
    _seed_org(sa_conn, "acme")
    relay_db.ensure_relay_client(sa_conn, "acme", enabled=1)
    m = _seed_member(sa_conn, "op")
    t1 = _seed_tweet(sa_conn, "p1", "auth1")
    t2 = _seed_tweet(sa_conn, "p2", "auth2")
    t3 = _seed_tweet(sa_conn, "p3", "auth3")
    _seed_submission(sa_conn, "acme", t1, m, "pending")
    _seed_submission(sa_conn, "acme", t2, m, "ready_to_publish")
    _seed_submission(sa_conn, "acme", t3, m, "published")  # terminal — excluded

    rows = relay_db.list_pending_submissions(sa_conn, "acme")
    statuses = {r["status"] for r in rows}
    assert statuses == {"pending", "ready_to_publish"}
    assert {r["x_id"] for r in rows} == {"p1", "p2"}
    assert all("x_author_handle" in r for r in rows)


def test_get_relay_status_summary(sa_conn):
    _seed_org(sa_conn, "acme")
    relay_db.ensure_relay_client(sa_conn, "acme", enabled=1)
    m = _seed_member(sa_conn, "op")
    t = _seed_tweet(sa_conn, "s1")
    _seed_submission(sa_conn, "acme", t, m, "pending")
    _seed_job(sa_conn, "acme", t, "claimed")
    relay_db.bind_chat(
        sa_conn, org_id="acme", platform="telegram", chat_id="-100", role="operator"
    )

    status = relay_db.get_relay_status(sa_conn, "acme")
    assert status is not None
    assert status["enabled"] == 1
    assert status["active_bindings"] == 1
    assert status["pending_submissions"] == 1
    assert status["inflight_jobs"] == 1


def test_get_relay_status_none_for_unknown(sa_conn):
    assert relay_db.get_relay_status(sa_conn, "nope") is None


def test_org_exists(sa_conn):
    _seed_org(sa_conn, "acme")
    assert relay_db.org_exists(sa_conn, "acme") is True
    assert relay_db.org_exists(sa_conn, "ghost") is False


@pytest.mark.parametrize("bad_state", ["halted", "paused", "frozen"])
def test_publication_jobs_check_rejects_invented_halted_states(sa_conn, bad_state):
    """Sanity: the schema CHECK rejects any invented halted state literal.

    This is why the kill-switch must use 'dead' — the only CHECK-allowed halted
    value. A direct write of 'halted'/'paused'/'frozen' raises.
    """
    _seed_org(sa_conn, "acme")
    relay_db.ensure_relay_client(sa_conn, "acme", enabled=1)
    t = _seed_tweet(sa_conn, "chk1")
    with pytest.raises(Exception):
        sa_conn.execute(
            text(
                "INSERT INTO relay_publication_jobs "
                "(org_id, tweet_id, destination_platform, destination_chat_id, state) "
                "VALUES ('acme', :t, 'discord', 'c', :s)"
            ),
            {"t": t, "s": bad_state},
        )
