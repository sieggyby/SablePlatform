"""Migration 064 — trending-story autopilot CRUD tests (sable_platform.relay.db).

Exercises the Stage A/B substrate against the in-memory ``sa_conn`` schema:
  * app-level dedup upsert (NO UNIQUE — a recurring story collapses to ONE row
    matched by normalized label / member-id overlap / monitor-term overlap, and
    merges its member ids + monitor terms, extends expiry to the later, and lifts
    emerging->active on a re-sighting);
  * distinct stories stay separate;
  * COALESCE-preserve on a None re-score;
  * the live-stories read (archived excluded, newest activity first);
  * decay (expired -> archived; NULL-expiry + future-expiry untouched);
  * the no-cost-column rule.
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


def _story_row(conn, sid: int) -> dict:
    row = conn.execute(
        text(
            "SELECT id, org_id, label, summary, relevance, momentum, "
            "       member_tweet_ids_json, monitor_terms_json, status, "
            "       first_seen_at, last_seen_at, expires_at, created_at "
            "FROM relay_trending_stories WHERE id = :i"
        ),
        {"i": sid},
    ).fetchone()
    return dict(row._mapping)


def _count(conn, org_id="orgA") -> int:
    return conn.execute(
        text("SELECT COUNT(*) FROM relay_trending_stories WHERE org_id = :o"), {"o": org_id}
    ).fetchone()[0]


# ==========================================================================
# Insert
# ==========================================================================
def test_upsert_trending_story_inserts(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        sid = relay_db.upsert_trending_story(
            sa_conn,
            org_id="orgA",
            label="EigenLayer OpenSwarm launch",
            summary="Sreeram's swarm announcement",
            relevance=88.0,
            momentum=12.0,
            member_tweet_ids_json=json.dumps([10, 11]),
            monitor_terms_json=json.dumps(["from:sreeramkannan", "eigenlayer swarm"]),
            expires_at="2026-06-10T00:00:00Z",
        )
    row = _story_row(sa_conn, sid)
    assert row["org_id"] == "orgA"
    assert row["label"] == "EigenLayer OpenSwarm launch"
    assert row["summary"] == "Sreeram's swarm announcement"
    assert row["relevance"] == 88.0
    assert row["momentum"] == 12.0
    assert row["status"] == "emerging"  # default on first detect
    assert json.loads(row["member_tweet_ids_json"]) == [10, 11]
    assert json.loads(row["monitor_terms_json"]) == ["from:sreeramkannan", "eigenlayer swarm"]
    assert row["expires_at"] == "2026-06-10T00:00:00Z"
    assert row["first_seen_at"] and row["last_seen_at"]


def test_upsert_trending_story_rejects_bad_status(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        try:
            relay_db.upsert_trending_story(sa_conn, org_id="orgA", label="x", status="bogus")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "bogus" in str(e)


# ==========================================================================
# Dedup
# ==========================================================================
def test_dedup_by_normalized_label_merges_and_lifts_status(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        sid1 = relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="EigenLayer OpenSwarm Launch",
            relevance=80.0, momentum=5.0,
            member_tweet_ids_json=json.dumps([1, 2]),
            monitor_terms_json=json.dumps(["t1 alpha", "t2 beta"]),
            expires_at="2026-06-10T00:00:00Z",
        )
    # Same story, different case / punctuation / whitespace label.
    with immediate_txn(sa_conn):
        sid2 = relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="eigenlayer   openswarm  launch!!!",
            relevance=91.0, momentum=20.0,
            member_tweet_ids_json=json.dumps([2, 3]),
            monitor_terms_json=json.dumps(["t2 beta", "t3 gamma"]),
            expires_at="2026-06-08T00:00:00Z",
        )
    assert sid2 == sid1  # collapsed onto the same row
    assert _count(sa_conn) == 1
    row = _story_row(sa_conn, sid1)
    assert row["status"] == "active"  # emerging -> active on re-sighting
    assert row["relevance"] == 91.0  # refreshed
    assert row["momentum"] == 20.0
    assert json.loads(row["member_tweet_ids_json"]) == [1, 2, 3]  # merged, order-preserved
    assert json.loads(row["monitor_terms_json"]) == ["t1 alpha", "t2 beta", "t3 gamma"]
    assert row["expires_at"] == "2026-06-10T00:00:00Z"  # later of the two kept


def test_dedup_by_member_overlap(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        sid1 = relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="Story One",
            member_tweet_ids_json=json.dumps([5, 6]),
            monitor_terms_json=json.dumps(["only here"]),
        )
    # Different label, different terms, but shares member id 5 (>= 1 -> match).
    with immediate_txn(sa_conn):
        sid2 = relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="Totally Different Headline",
            member_tweet_ids_json=json.dumps([5, 7]),
            monitor_terms_json=json.dumps(["different term"]),
        )
    assert sid2 == sid1
    assert _count(sa_conn) == 1
    assert json.loads(_story_row(sa_conn, sid1)["member_tweet_ids_json"]) == [5, 6, 7]


def test_dedup_by_term_overlap(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        sid1 = relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="Story One",
            member_tweet_ids_json=json.dumps([1]),
            monitor_terms_json=json.dumps(["eigenlayer swarm", "from:sreeramkannan"]),
        )
    # Different label, disjoint members, but shares >= 2 normalized monitor terms.
    with immediate_txn(sa_conn):
        sid2 = relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="Unrelated Title",
            member_tweet_ids_json=json.dumps([99]),
            monitor_terms_json=json.dumps(["EigenLayer Swarm", "from:sreeramkannan", "extra"]),
        )
    assert sid2 == sid1
    assert _count(sa_conn) == 1


def test_one_shared_term_is_not_enough(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="Story One",
            member_tweet_ids_json=json.dumps([1]),
            monitor_terms_json=json.dumps(["swarm", "alpha"]),
        )
    # Shares exactly ONE term (< _STORY_TERM_OVERLAP_MIN) and nothing else -> distinct.
    with immediate_txn(sa_conn):
        relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="Story Two",
            member_tweet_ids_json=json.dumps([2]),
            monitor_terms_json=json.dumps(["swarm", "beta"]),
        )
    assert _count(sa_conn) == 2


def test_distinct_stories_stay_separate(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="Alpha story",
            member_tweet_ids_json=json.dumps([1, 2]),
            monitor_terms_json=json.dumps(["alpha foo", "alpha bar"]),
        )
    with immediate_txn(sa_conn):
        relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="Beta story",
            member_tweet_ids_json=json.dumps([3, 4]),
            monitor_terms_json=json.dumps(["beta baz", "beta qux"]),
        )
    assert _count(sa_conn) == 2


def test_resight_with_none_preserves_existing_summary(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        sid = relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="S", summary="first summary",
            member_tweet_ids_json=json.dumps([1]),
        )
    with immediate_txn(sa_conn):
        relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="S", summary=None,
            member_tweet_ids_json=json.dumps([1]),
        )
    assert _story_row(sa_conn, sid)["summary"] == "first summary"  # COALESCE-preserved


def test_org_isolation_in_dedup(sa_conn):
    _seed(sa_conn, org_id="orgA")
    _seed(sa_conn, org_id="orgB")
    sa_conn.commit()
    with immediate_txn(sa_conn):
        relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="Shared label",
            member_tweet_ids_json=json.dumps([1]),
        )
    # Same label for a DIFFERENT org must NOT dedup onto orgA's row.
    with immediate_txn(sa_conn):
        relay_db.upsert_trending_story(
            sa_conn, org_id="orgB", label="Shared label",
            member_tweet_ids_json=json.dumps([1]),
        )
    assert _count(sa_conn, "orgA") == 1
    assert _count(sa_conn, "orgB") == 1


# ==========================================================================
# Read + decay
# ==========================================================================
def test_list_live_excludes_archived_newest_first(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        old = relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="Older", now="2026-06-01T00:00:00Z",
            member_tweet_ids_json=json.dumps([1]),
        )
        new = relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="Newer", now="2026-06-03T00:00:00Z",
            member_tweet_ids_json=json.dumps([2]),
        )
        archived = relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="Gone", status="archived",
            member_tweet_ids_json=json.dumps([3]),
        )
    live = relay_db.list_live_trending_stories(sa_conn, "orgA")
    ids = [s["id"] for s in live]
    assert archived not in ids
    assert ids == [new, old]  # newest last_seen first


def test_decay_archives_only_expired(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        expired = relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="Expired",
            member_tweet_ids_json=json.dumps([1]), expires_at="2020-01-01T00:00:00Z",
        )
        future = relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="Future",
            member_tweet_ids_json=json.dumps([2]), expires_at="2030-01-01T00:00:00Z",
        )
        never = relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="NoExpiry",
            member_tweet_ids_json=json.dumps([3]), expires_at=None,
        )
    with immediate_txn(sa_conn):
        n = relay_db.decay_trending_stories(sa_conn, "orgA", now="2026-06-03T00:00:00Z")
    assert n == 1
    assert _story_row(sa_conn, expired)["status"] == "archived"
    assert _story_row(sa_conn, future)["status"] != "archived"
    assert _story_row(sa_conn, never)["status"] != "archived"


def test_dedup_adopts_later_or_first_expiry(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    # First story has NO expiry; re-sight supplies one -> adopted.
    with immediate_txn(sa_conn):
        sid = relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="S", member_tweet_ids_json=json.dumps([1]),
            expires_at=None,
        )
    with immediate_txn(sa_conn):
        relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="S", member_tweet_ids_json=json.dumps([1]),
            expires_at="2026-06-12T00:00:00Z",
        )
    assert _story_row(sa_conn, sid)["expires_at"] == "2026-06-12T00:00:00Z"
    # Re-sight with a LATER expiry -> adopted (extends monitoring).
    with immediate_txn(sa_conn):
        relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="S", member_tweet_ids_json=json.dumps([1]),
            expires_at="2026-06-20T00:00:00Z",
        )
    assert _story_row(sa_conn, sid)["expires_at"] == "2026-06-20T00:00:00Z"
    # Re-sight with an EARLIER expiry -> the later one is kept (never shortened).
    with immediate_txn(sa_conn):
        relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="S", member_tweet_ids_json=json.dumps([1]),
            expires_at="2026-06-15T00:00:00Z",
        )
    assert _story_row(sa_conn, sid)["expires_at"] == "2026-06-20T00:00:00Z"


def test_resight_does_not_downgrade_a_live_status(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        sid = relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="S", status="active",
            member_tweet_ids_json=json.dumps([1]),
        )
    with immediate_txn(sa_conn):
        relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="S", member_tweet_ids_json=json.dumps([1]),
        )
    assert _story_row(sa_conn, sid)["status"] == "active"  # not flipped, not downgraded


def test_decay_uses_utcnow_when_now_omitted(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        sid = relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="Old", member_tweet_ids_json=json.dumps([1]),
            expires_at="2020-01-01T00:00:00Z",  # long past relative to real utcnow
        )
    with immediate_txn(sa_conn):
        n = relay_db.decay_trending_stories(sa_conn, "orgA")  # no now= -> _utc_now_iso()
    assert n == 1
    assert _story_row(sa_conn, sid)["status"] == "archived"


def test_no_cost_shaped_column(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        relay_db.upsert_trending_story(
            sa_conn, org_id="orgA", label="S", member_tweet_ids_json=json.dumps([1])
        )
    import re

    cost_re = re.compile(r"cost|usd|price|spend|budget|token", re.I)
    for story in relay_db.list_live_trending_stories(sa_conn, "orgA"):
        for key in story.keys():
            assert not cost_re.search(key), f"cost-shaped column leaked: {key}"
