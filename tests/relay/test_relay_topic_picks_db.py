"""Migration 072 — Tweet Assist compose topic-pick feedback-loop CRUD tests.

Exercises ``record_topic_pick`` / ``recent_topic_picks`` against the in-memory
``sa_conn`` schema:
  * record appends a pick; recent reads it back;
  * append-only (no dedup — a repeat pick is two rows, both signal);
  * recency window + newest-first ordering + limit;
  * org-scoped (orgA's picks never leak to orgB);
  * the no-cost-column rule (a pick is a usage signal, cost lives only in cost_events).
"""
from __future__ import annotations

import datetime as _dt

from sqlalchemy import text

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.txn import immediate_txn


def _seed(conn, *, org_id="orgA"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled, config) VALUES (:o, 1, '{}')"),
        {"o": org_id},
    )


def test_record_and_recent(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    assert relay_db.recent_topic_picks(sa_conn, "orgA") == []
    # picked_at must sit INSIDE recent_topic_picks' default 30d window, which is computed from the
    # REAL current date — a hardcoded date silently ages out and the test starts failing on a
    # calendar boundary (it did: seeded 2026-06-07, first failed 2026-07-07).
    picked_at = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    with immediate_txn(sa_conn):
        pid = relay_db.record_topic_pick(
            sa_conn, org_id="orgA", topic="rollups season", register_band="serious",
            operator_handle="operator_arf", now=picked_at,
        )
    assert pid > 0
    picks = relay_db.recent_topic_picks(sa_conn, "orgA")
    assert len(picks) == 1
    assert picks[0]["topic"] == "rollups season"
    assert picks[0]["register_band"] == "serious"
    assert picks[0]["picked_at"] == picked_at


def test_append_only_no_dedup(sa_conn):
    # A repeat pick of the same topic is TWO rows — a repeat is itself signal.
    _seed(sa_conn)
    sa_conn.commit()
    for ts in ("2026-06-07T00:00:00Z", "2026-06-07T01:00:00Z"):
        with immediate_txn(sa_conn):
            relay_db.record_topic_pick(sa_conn, org_id="orgA", topic="gm", now=ts)
    n = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_topic_picks WHERE org_id = 'orgA' AND topic = 'gm'")
    ).fetchone()[0]
    assert n == 2


def test_recent_window_and_order(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    # An old pick (well outside the default 30d window) and two recent ones.
    rows = [
        ("ancient", "2020-01-01T00:00:00Z"),
        ("older", "2026-06-05T00:00:00Z"),
        ("newest", "2026-06-07T00:00:00Z"),
    ]
    for topic, ts in rows:
        with immediate_txn(sa_conn):
            relay_db.record_topic_pick(sa_conn, org_id="orgA", topic=topic, now=ts)
    # A wide window keeps all three, newest first.
    wide = [p["topic"] for p in relay_db.recent_topic_picks(sa_conn, "orgA", days=9999)]
    assert wide == ["newest", "older", "ancient"]
    # The limit caps the list (still newest-first).
    assert [p["topic"] for p in relay_db.recent_topic_picks(sa_conn, "orgA", days=9999, limit=1)] == ["newest"]


def test_org_scoped(sa_conn):
    _seed(sa_conn, org_id="orgA")
    _seed(sa_conn, org_id="orgB")
    sa_conn.commit()
    with immediate_txn(sa_conn):
        relay_db.record_topic_pick(sa_conn, org_id="orgA", topic="A-topic")
    with immediate_txn(sa_conn):
        relay_db.record_topic_pick(sa_conn, org_id="orgB", topic="B-topic")
    assert [p["topic"] for p in relay_db.recent_topic_picks(sa_conn, "orgA", days=9999)] == ["A-topic"]
    assert [p["topic"] for p in relay_db.recent_topic_picks(sa_conn, "orgB", days=9999)] == ["B-topic"]


def test_no_cost_column(sa_conn):
    rows = sa_conn.execute(text("PRAGMA table_info(relay_topic_picks)")).fetchall()
    names = {r._mapping["name"] for r in rows}
    assert not any("cost" in n.lower() for n in names), names
    assert names == {"id", "org_id", "topic", "register_band", "operator_handle", "picked_at"}
