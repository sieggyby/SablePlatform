"""Weekly duel-pool enrichment — promote cached tweets into the /duel game (FREE)."""
from __future__ import annotations

import json

from sqlalchemy import text

from sable_platform import duel_enrichment as de
from sable_platform.db import content_deck as cd


def _seed_org(conn, org="tig"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org})


def _relay(conn, x_id, handle, txt, *, likes=0, rts=0, replies=0, quotes=0,
           lang="en", posted_at="2026-07-10T00:00:00Z", is_reply=False, raw=None):
    eng = {"likes": likes, "retweets": rts, "replies": replies, "quotes": quotes,
           "bookmarks": 0, "views": likes * 10}
    conn.execute(
        text("INSERT INTO relay_tweets (x_id, x_author_handle, text, engagement_json, lang, "
             "posted_at, is_reply, raw, source, fetched_at) "
             "VALUES (:x, :h, :t, :e, :l, :p, :r, :raw, 'test', '2026-07-12T00:00:00Z')"),
        {"x": x_id, "h": handle, "t": txt, "e": json.dumps(eng), "l": lang,
         "p": posted_at, "r": is_reply, "raw": raw or "{}"},
    )


def _cands(conn, org="tig"):
    rows = conn.execute(text(
        "SELECT payload_json, source, score FROM content_candidates "
        "WHERE org_id = :o AND kind = 'community_tweet'"), {"o": org}).fetchall()
    return [(json.loads(r[0]), r[1], r[2]) for r in rows]


def test_promotes_only_relevant_popped_tweets(sa_conn):
    _seed_org(sa_conn)
    _relay(sa_conn, "1", "alice", "gm $TIG is popping", likes=40, rts=10)      # relevant + popped
    _relay(sa_conn, "2", "bob", "just some random defi post", likes=99, rts=99)  # off-topic
    _relay(sa_conn, "3", "carol", "$tig looking weak", likes=1, rts=0)          # relevant but dead
    sa_conn.commit()
    summary = de.enrich_duel_pool(sa_conn, "tig", terms=("$tig",), min_popped=15,
                                  now="2026-07-12T00:00:00Z")
    cands = _cands(sa_conn)
    xids = {c[0]["x_id"] for c in cands}
    assert xids == {"1"}                       # off-topic + dead excluded
    assert summary["added"] == 1
    assert cands[0][0]["engagement"] == {"likes": 40, "retweets": 10, "replies": 0, "quotes": 0}
    assert "image_url" not in cands[0][0]      # TEXT-only — memes are human-curated


def test_author_allowlist_admits_no_term_match(sa_conn):
    _seed_org(sa_conn)
    _relay(sa_conn, "10", "vidalthi", "a thread about vehicle routing", likes=50, rts=5)
    sa_conn.commit()
    de.enrich_duel_pool(sa_conn, "tig", terms=("$tig",), authors=("vidalthi",),
                        min_popped=15, now="2026-07-12T00:00:00Z")
    assert {c[0]["x_id"] for c in _cands(sa_conn)} == {"10"}


def test_dedup_on_reruns(sa_conn):
    _seed_org(sa_conn)
    _relay(sa_conn, "1", "alice", "$tig gm", likes=40, rts=10)
    sa_conn.commit()
    de.enrich_duel_pool(sa_conn, "tig", terms=("$tig",), now="2026-07-12T00:00:00Z")
    s2 = de.enrich_duel_pool(sa_conn, "tig", terms=("$tig",), now="2026-07-12T00:00:00Z")
    assert s2["added"] == 0 and len(_cands(sa_conn)) == 1  # never re-add a seen tweet


def test_cap_keeps_highest_engagement_first(sa_conn):
    _seed_org(sa_conn)
    for i, likes in enumerate([20, 90, 50], start=1):
        _relay(sa_conn, str(i), f"u{i}", f"$tig post {i}", likes=likes)
    sa_conn.commit()
    de.enrich_duel_pool(sa_conn, "tig", terms=("$tig",), min_popped=15, max_add=2,
                        now="2026-07-12T00:00:00Z")
    scores = sorted((c[0]["engagement"]["likes"] for c in _cands(sa_conn)), reverse=True)
    assert scores == [90, 50]  # the two most-popped, the 20-like one capped out


def test_stale_tweet_excluded(sa_conn):
    _seed_org(sa_conn)
    _relay(sa_conn, "1", "alice", "$tig ancient", likes=99, posted_at="2025-01-01T00:00:00Z")
    sa_conn.commit()
    s = de.enrich_duel_pool(sa_conn, "tig", terms=("$tig",), lookback_days=45,
                            now="2026-07-12T00:00:00Z")
    assert s["added"] == 0 and s["skip_stale"] == 1


def test_replies_excluded(sa_conn):
    _seed_org(sa_conn)
    _relay(sa_conn, "1", "alice", "$tig reply", likes=99, is_reply=True)
    sa_conn.commit()
    s = de.enrich_duel_pool(sa_conn, "tig", terms=("$tig",), now="2026-07-12T00:00:00Z")
    assert s["added"] == 0
