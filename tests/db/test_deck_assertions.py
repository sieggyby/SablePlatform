"""Single-use deck/produce assertion store (migration 079) — Codex Tier-1 replay defense.

``consume_assertion`` claims an assertion SIGNATURE exactly once; a replay (the SAME sig, even with a
different request payload) must be rejected. ``gc_expired_assertions`` prunes dead rows.
"""
from __future__ import annotations

import pytest

from sable_platform.db.deck_assertions import consume_assertion, gc_expired_assertions
from tests.conftest import make_test_conn, make_test_file_db


@pytest.fixture
def conn():
    c = make_test_conn(with_org="tig")
    yield c
    c.close()


def test_first_use_succeeds_replay_is_rejected(conn):
    ok = consume_assertion(conn, sig="abc123", action="produce", org_id="tig",
                           actor="operator_sieg", exp=2000000)
    assert ok is True  # first use -> caller may proceed
    # A replay of the SAME signature (a captured-but-valid assertion re-POSTed) is rejected.
    assert consume_assertion(conn, sig="abc123", action="produce", org_id="tig",
                             actor="operator_sieg", exp=2000000) is False


def test_replay_with_tampered_request_fields_is_still_rejected(conn):
    # The signature is what's consumed, so a replay that mutates the UNSIGNED request fields (a
    # different action/org/actor/exp on the SAME captured sig) re-presents the same sig and 403s.
    assert consume_assertion(conn, sig="sig-X", action="schedule", org_id="tig",
                             actor="operator_sieg", exp=2000000) is True
    assert consume_assertion(conn, sig="sig-X", action="post", org_id="psy",
                             actor="operator_eve", exp=2000050) is False


def test_distinct_signatures_are_independent(conn):
    assert consume_assertion(conn, sig="s1", action="produce", org_id="tig",
                             actor="op", exp=2000000) is True
    assert consume_assertion(conn, sig="s2", action="produce", org_id="tig",
                             actor="op", exp=2000000) is True  # different sig -> independent


def test_consume_persists_and_is_visible_to_a_fresh_connection(tmp_path):
    # Single-use must be DURABLE + cross-connection (race/replay-safe across workers), not an
    # in-process cache: a fresh connection to the same file DB sees the consumed sig.
    db = str(tmp_path / "sable.db")
    c1 = make_test_file_db(db, with_org="tig")
    try:
        assert consume_assertion(c1, sig="durable-sig", action="produce", org_id="tig",
                                 actor="op", exp=2000000) is True
    finally:
        c1.close()
    c2 = make_test_file_db(db)
    try:
        assert consume_assertion(c2, sig="durable-sig", action="produce", org_id="tig",
                                 actor="op", exp=2000000) is False  # already consumed on disk
    finally:
        c2.close()


def test_gc_prunes_only_long_expired_rows(conn):
    consume_assertion(conn, sig="old", action="produce", org_id="tig", actor="op", exp=1000)
    consume_assertion(conn, sig="new", action="produce", org_id="tig", actor="op", exp=5000)
    from sable_platform.relay.bot.txn import immediate_txn
    sa_conn = getattr(conn, "_conn", conn)
    with immediate_txn(sa_conn):
        removed = gc_expired_assertions(conn, now_unix=5000, grace_seconds=1000)
    assert removed == 1  # 'old' (exp 1000 < 5000-1000) pruned; 'new' (exp 5000) kept
    rows = conn.execute("SELECT sig FROM deck_consumed_assertions").fetchall()
    assert {r[0] for r in rows} == {"new"}
