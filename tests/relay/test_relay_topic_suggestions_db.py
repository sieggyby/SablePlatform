"""Migration 071 — Tweet Assist compose topic-suggestion cache CRUD tests.

Exercises ``replace_topic_suggestions`` / ``get_topic_suggestions`` against the
in-memory ``sa_conn`` schema:
  * replace inserts a current row; get reads it back;
  * ONE current row per org (a second replace collapses to one, latest wins);
  * empty read returns None;
  * org-scoped (orgA's topics never leak to orgB);
  * the no-cost-column rule (cost lives only in cost_events).
"""
from __future__ import annotations

import json

from sqlalchemy import text

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.txn import immediate_txn


def _seed(conn, *, org_id="orgA"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled, config) VALUES (:o, 1, '{}')"),
        {"o": org_id},
    )


_TOPICS = json.dumps([
    {"topic": "rollups season", "angle": "why usage is the valuation",
     "register_band": "serious", "why": "trending", "sources": ["trending_story"]},
    {"topic": "gm", "angle": "low-effort but on-brand", "register_band": "shitpost",
     "why": "lexicon", "sources": ["lexicon"]},
])


def test_replace_and_get(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    assert relay_db.get_topic_suggestions(sa_conn, "orgA") is None
    with immediate_txn(sa_conn):
        rid = relay_db.replace_topic_suggestions(
            sa_conn, org_id="orgA", topics_json=_TOPICS, model="claude-sonnet-4-6",
            now="2026-06-06T00:00:00Z",
        )
    assert rid > 0
    got = relay_db.get_topic_suggestions(sa_conn, "orgA")
    assert got is not None
    assert got["org_id"] == "orgA"
    assert got["model"] == "claude-sonnet-4-6"
    assert got["refreshed_at"] == "2026-06-06T00:00:00Z"
    topics = json.loads(got["topics_json"])
    assert [t["topic"] for t in topics] == ["rollups season", "gm"]
    assert topics[1]["register_band"] == "shitpost"


def test_replace_keeps_one_row_per_org(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        relay_db.replace_topic_suggestions(
            sa_conn, org_id="orgA", topics_json='[{"topic":"old"}]', now="2026-06-06T00:00:00Z")
    with immediate_txn(sa_conn):
        relay_db.replace_topic_suggestions(
            sa_conn, org_id="orgA", topics_json='[{"topic":"new"}]', now="2026-06-06T01:00:00Z")
    n = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_topic_suggestions WHERE org_id = 'orgA'")
    ).fetchone()[0]
    assert n == 1  # delete-then-insert keeps exactly one current row
    got = relay_db.get_topic_suggestions(sa_conn, "orgA")
    assert json.loads(got["topics_json"])[0]["topic"] == "new"  # latest wins


def test_org_scoped(sa_conn):
    _seed(sa_conn, org_id="orgA")
    _seed(sa_conn, org_id="orgB")
    sa_conn.commit()
    with immediate_txn(sa_conn):
        relay_db.replace_topic_suggestions(sa_conn, org_id="orgA", topics_json='[{"topic":"A"}]')
    with immediate_txn(sa_conn):
        relay_db.replace_topic_suggestions(sa_conn, org_id="orgB", topics_json='[{"topic":"B"}]')
    assert json.loads(relay_db.get_topic_suggestions(sa_conn, "orgA")["topics_json"])[0]["topic"] == "A"
    assert json.loads(relay_db.get_topic_suggestions(sa_conn, "orgB")["topics_json"])[0]["topic"] == "B"


def test_no_cost_column(sa_conn):
    # The cache must never carry a cost column (cost lives only in cost_events).
    rows = sa_conn.execute(text("PRAGMA table_info(relay_topic_suggestions)")).fetchall()
    names = {r._mapping["name"] for r in rows}
    assert not any("cost" in n.lower() for n in names), names
    assert names == {"id", "org_id", "topics_json", "model", "refreshed_at", "created_at"}
