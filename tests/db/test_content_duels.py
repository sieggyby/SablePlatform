"""mig 084 — durable open-duel registry (sable_platform.db.content_duels).

Exercises the restart-durability primitives: open→lookup, the per-channel lock, the due
sweep (incl. expired-during-downtime), single-flight close, and the vote tally counted
from content_deck_decisions since opened_at.
"""
from __future__ import annotations

import json

from sqlalchemy import text

from sable_platform.db import content_deck as cd
from sable_platform.db import content_duels as cdu
from sable_platform.relay.bot.txn import immediate_txn


def _seed(conn, *orgs):
    for o in orgs or ("orgA",):
        conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": o})
    conn.commit()


def _card(cid, author="a"):
    return json.dumps({"id": cid, "kind": "community_tweet", "text": f"t{cid}",
                       "author": author, "engagement": {"likes": 1}})


def _open(conn, mid, *, org="orgA", channel="500", opened="2026-07-11T00:00:00Z",
          deadline="2026-07-12T00:00:00Z", a=1, b=2):
    with immediate_txn(conn):
        cdu.open_duel(conn, message_id=mid, org_id=org, guild_id="100", channel_id=channel,
                      card_a_json=_card(a), card_b_json=_card(b), opened_at=opened, deadline=deadline)


def test_open_and_get(sa_conn):
    _seed(sa_conn)
    _open(sa_conn, "m1")
    d = cdu.get_duel(sa_conn, "m1")
    assert d["message_id"] == "m1" and d["status"] == "open" and d["channel_id"] == "500"
    assert json.loads(d["card_a_json"])["id"] == 1
    assert cdu.get_duel(sa_conn, "nope") is None


def test_channel_lock(sa_conn):
    _seed(sa_conn)
    assert cdu.channel_has_open_duel(sa_conn, "500") is False
    _open(sa_conn, "m1", channel="500")
    assert cdu.channel_has_open_duel(sa_conn, "500") is True   # live in 500
    assert cdu.channel_has_open_duel(sa_conn, "600") is False  # but not 600
    # a CLOSED duel frees the channel
    with immediate_txn(sa_conn):
        cdu.close_duel(sa_conn, "m1")
    assert cdu.channel_has_open_duel(sa_conn, "500") is False


def test_list_due_includes_expired(sa_conn):
    _seed(sa_conn)
    _open(sa_conn, "past", deadline="2020-01-01T00:00:00Z")     # long expired (downtime case)
    _open(sa_conn, "future", channel="501", deadline="2099-01-01T00:00:00Z")
    due = cdu.list_due_duels(sa_conn, now="2026-07-11T12:00:00Z")
    assert [d["message_id"] for d in due] == ["past"]           # only the past-deadline one


def test_close_is_single_flight(sa_conn):
    _seed(sa_conn)
    _open(sa_conn, "m1")
    with immediate_txn(sa_conn):
        assert cdu.close_duel(sa_conn, "m1") is True   # first claim wins
    with immediate_txn(sa_conn):
        assert cdu.close_duel(sa_conn, "m1") is False  # second is a no-op (never double-reveal)
    assert cdu.get_duel(sa_conn, "m1")["status"] == "closed"


def test_count_duel_votes_from_ledger(sa_conn):
    _seed(sa_conn)
    # two candidates + community votes recorded in content_deck_decisions
    with immediate_txn(sa_conn):
        a = cd.upsert_candidate(sa_conn, org_id="orgA", kind="community_tweet",
                                payload_json='{"text":"a"}', source="s")
        b = cd.upsert_candidate(sa_conn, org_id="orgA", kind="community_tweet",
                                payload_json='{"text":"b"}', source="s")
    # 2 votes for A, 1 for B, all after opened_at
    with immediate_txn(sa_conn):
        for actor in ("u1", "u2"):
            cd.record_deck_decision(sa_conn, candidate_id=a, org_id="orgA",
                                    actor=f"discord:user:{actor}", actor_kind="community",
                                    decision="keep", surface="discord", pair_loser_id=b)
        cd.record_deck_decision(sa_conn, candidate_id=b, org_id="orgA",
                                actor="discord:user:u3", actor_kind="community",
                                decision="keep", surface="discord", pair_loser_id=a)
    va, vb = cdu.count_duel_votes(sa_conn, "orgA", a, b, "2000-01-01T00:00:00Z")
    assert (va, vb) == (2, 1)
    # a vote BEFORE the since window is excluded
    va2, vb2 = cdu.count_duel_votes(sa_conn, "orgA", a, b, "2999-01-01T00:00:00Z")
    assert (va2, vb2) == (0, 0)
