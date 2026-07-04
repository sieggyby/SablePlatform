"""The shared SocialData cache (mig 082, `relay/tweet_cache.py`).

Load-bearing claims: the Layer-A serve rule (fresh-fetch OR plateaued; unknown
posted_at NEVER plateaus — pre-082 rows fail open to a live fetch); write-through
normalization incl. posted_at parsing + COALESCE preservation; Layer-B window
finality (open windows refused; closed windows immutable; ANY missing member = a
whole-window miss, never a silently-partial result)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from sable_platform.relay import tweet_cache as tc
from sable_platform.relay.bot.txn import immediate_txn

NOW = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _raw(tid: str, *, handle="alice", text_body="hello world", posted=None, likes=5):
    t = {
        "id_str": tid,
        "full_text": text_body,
        "user": {"id_str": "9001", "screen_name": handle, "followers_count": 123},
        "favorite_count": likes,
        "retweet_count": 1,
        "reply_count": 0,
        "lang": "en",
    }
    if posted is not None:
        t["tweet_created_at"] = _iso(posted)
    return t


def _write(conn, raws, source="test"):
    with immediate_txn(conn):
        out = tc.upsert_socialdata_tweets(conn, raws, source=source)
    conn.commit()
    return out


def _set_fetched_at(conn, x_id, dt):
    conn.execute(
        text("UPDATE relay_tweets SET fetched_at = :f WHERE x_id = :x"),
        {"f": _iso(dt), "x": x_id},
    )
    conn.commit()


# --- write-through normalization ---------------------------------------------

def test_upsert_normalizes_and_stamps(sa_conn):
    posted = NOW - timedelta(days=30)
    ids = _write(sa_conn, [_raw("111", posted=posted)])
    assert "111" in ids
    row = sa_conn.execute(
        text("SELECT x_author_handle, posted_at, source, engagement_json, author_followers "
             "FROM relay_tweets WHERE x_id = '111'")
    ).fetchone()
    assert row[0] == "alice"
    assert row[1] == _iso(posted)
    assert row[2] == "test"
    assert json.loads(row[3])["likes"] == 5
    assert row[4] == 123


def test_upsert_parses_legacy_ctime_posted_at(sa_conn):
    t = _raw("112")
    t["tweet_created_at"] = "Wed Oct 10 20:19:24 +0000 2018"
    _write(sa_conn, [t])
    row = sa_conn.execute(
        text("SELECT posted_at FROM relay_tweets WHERE x_id = '112'")
    ).fetchone()
    assert row[0] == "2018-10-10T20:19:24Z"


def test_upsert_coalesce_semantics(sa_conn):
    """posted_at: a later write WITHOUT a parseable creation time never clobbers a
    known value (COALESCE new→old). source: the LAST fetcher's provenance wins when
    non-null — the row's current raw/engagement came from them. Engagement refreshes."""
    posted = NOW - timedelta(days=3)
    _write(sa_conn, [_raw("113", posted=posted)], source="cult_grader")
    t2 = _raw("113", likes=9)
    t2.pop("tweet_created_at", None)
    _write(sa_conn, [t2], source="sweep")
    row = sa_conn.execute(
        text("SELECT posted_at, source, engagement_json FROM relay_tweets WHERE x_id='113'")
    ).fetchone()
    assert row[0] == _iso(posted)            # unknown-new never clobbers known-old
    assert row[1] == "sweep"                 # last non-null fetcher wins
    assert json.loads(row[2])["likes"] == 9  # engagement refreshed


def test_upsert_skips_malformed_entries(sa_conn):
    out = _write(sa_conn, [{"no": "id"}, "not a dict", _raw("114")])
    assert list(out) == ["114"]


# --- Layer A serve rule --------------------------------------------------------

def test_fresh_fetch_serves_regardless_of_age(sa_conn):
    _write(sa_conn, [_raw("221", posted=NOW - timedelta(hours=2))])
    _set_fetched_at(sa_conn, "221", NOW - timedelta(hours=1))
    assert tc.get_cached_tweet_raw(sa_conn, "221", now=NOW) is not None


def test_stale_fetch_young_tweet_misses(sa_conn):
    _write(sa_conn, [_raw("222", posted=NOW - timedelta(days=3))])
    _set_fetched_at(sa_conn, "222", NOW - timedelta(days=1))
    assert tc.get_cached_tweet_raw(sa_conn, "222", now=NOW) is None


def test_plateaued_tweet_serves_from_old_fetch(sa_conn):
    _write(sa_conn, [_raw("223", posted=NOW - timedelta(days=30))])
    _set_fetched_at(sa_conn, "223", NOW - timedelta(days=10))
    hit = tc.get_cached_tweet_raw(sa_conn, "223", now=NOW)
    assert hit is not None and hit["id_str"] == "223"


def test_unknown_posted_at_never_plateaus(sa_conn):
    """Pre-082 rows (posted_at NULL) must fail OPEN to a live fetch."""
    t = _raw("224")
    t.pop("tweet_created_at", None)
    _write(sa_conn, [t])
    _set_fetched_at(sa_conn, "224", NOW - timedelta(days=30))
    assert tc.get_cached_tweet_raw(sa_conn, "224", now=NOW) is None


def test_rawless_row_misses(sa_conn):
    _write(sa_conn, [_raw("225", posted=NOW - timedelta(days=30))])
    sa_conn.execute(text("UPDATE relay_tweets SET raw = NULL WHERE x_id = '225'"))
    sa_conn.commit()
    assert tc.get_cached_tweet_raw(sa_conn, "225", now=NOW) is None


def test_batch_read_returns_servable_subset(sa_conn):
    _write(sa_conn, [
        _raw("231", posted=NOW - timedelta(days=30)),   # plateaued → serves
        _raw("232", posted=NOW - timedelta(days=1)),    # young → miss (after fetch ages)
    ])
    for x in ("231", "232"):
        _set_fetched_at(sa_conn, x, NOW - timedelta(days=9))
    hits = tc.get_cached_tweets_raw(sa_conn, ["231", "232", "999"], now=NOW)
    assert set(hits) == {"231"}


# --- Layer B closed windows ------------------------------------------------------

def _mark(conn, *, query="@tig -from:tig", ws=None, we=None, ids=(), now=NOW):
    ws = ws or _iso(NOW - timedelta(days=40))
    we = we or _iso(NOW - timedelta(days=35))
    with immediate_txn(conn):
        ok = tc.mark_window_complete(
            conn, query=query, window_start=ws, window_end=we,
            x_ids=list(ids), source="test", now=now,
        )
    conn.commit()
    return ok, ws, we


def test_open_window_refused(sa_conn):
    ok, _, _ = _mark(sa_conn, we=_iso(NOW + timedelta(days=1)))
    assert ok is False
    n = sa_conn.execute(text("SELECT COUNT(*) FROM relay_search_windows")).fetchone()[0]
    assert n == 0


def test_window_roundtrip_hydrates_in_order(sa_conn):
    _write(sa_conn, [_raw("311", text_body="a"), _raw("312", text_body="b")])
    ok, ws, we = _mark(sa_conn, ids=["312", "311"])
    assert ok
    tweets = tc.get_completed_window(sa_conn, query="@TIG   -from:tig", window_start=ws, window_end=we)
    assert [t["id_str"] for t in tweets] == ["312", "311"]  # order kept; query normalized


def test_window_is_immutable_first_writer_wins(sa_conn):
    _write(sa_conn, [_raw("321")])
    ok1, ws, we = _mark(sa_conn, ids=["321"])
    ok2, _, _ = _mark(sa_conn, ids=["321", "322"])  # a repeat mark changes nothing
    assert ok1 and ok2
    tweets = tc.get_completed_window(sa_conn, query="@tig -from:tig", window_start=ws, window_end=we)
    assert [t["id_str"] for t in tweets] == ["321"]


def test_missing_member_is_a_whole_window_miss(sa_conn):
    _write(sa_conn, [_raw("331")])
    ok, ws, we = _mark(sa_conn, ids=["331", "332"])  # 332 never written through
    assert ok
    assert tc.get_completed_window(
        sa_conn, query="@tig -from:tig", window_start=ws, window_end=we
    ) is None


def test_empty_window_completes_and_returns_empty(sa_conn):
    ok, ws, we = _mark(sa_conn, ids=[])
    assert ok
    assert tc.get_completed_window(
        sa_conn, query="@tig -from:tig", window_start=ws, window_end=we
    ) == []


def test_unknown_window_misses(sa_conn):
    assert tc.get_completed_window(
        sa_conn, query="@nobody", window_start="2026-01-01T00:00:00Z",
        window_end="2026-01-06T00:00:00Z",
    ) is None


def test_date_only_window_bounds_work(sa_conn):
    """Cult Grader windows are bare DATES ('2026-05-28'). Naive parses must be
    treated as UTC (never raise naive-vs-aware), and a plateaued date-only window
    marks + hydrates cleanly."""
    _write(sa_conn, [_raw("411")])
    with immediate_txn(sa_conn):
        ok = tc.mark_window_complete(
            sa_conn, query="@tig -from:tig", window_start="2026-05-23",
            window_end="2026-05-28", x_ids=["411"], source="cult_grader",
            now=datetime(2026, 7, 3, 14, 0, 0, tzinfo=timezone.utc),
        )
    sa_conn.commit()
    assert ok is True
    got = tc.get_completed_window(
        sa_conn, query="@tig -from:tig", window_start="2026-05-23",
        window_end="2026-05-28",
    )
    assert [t["id_str"] for t in got] == ["411"]


def test_recent_closed_window_refused_until_plateau(sa_conn):
    """T2-1: a window that ENDED recently is closed as a SET but its members'
    engagement is still moving — freezing it in the immutable shared store would
    silently defeat consumers' own re-fetch healing (CG's drop-newest-window). Only
    windows ending ≥PLATEAU_DAYS ago are cacheable."""
    _write(sa_conn, [_raw("412")])
    with immediate_txn(sa_conn):
        ok = tc.mark_window_complete(
            sa_conn, query="@tig -from:tig",
            window_start=_iso(NOW - timedelta(days=8)),
            window_end=_iso(NOW - timedelta(days=3)),  # closed, but < 14d past
            x_ids=["412"], source="cult_grader", now=NOW,
        )
    sa_conn.commit()
    assert ok is False


def test_date_only_open_window_still_refused(sa_conn):
    with immediate_txn(sa_conn):
        ok = tc.mark_window_complete(
            sa_conn, query="@tig -from:tig", window_start="2026-07-01",
            window_end="2026-07-04", x_ids=[], source="cult_grader",
            now=datetime(2026, 7, 3, 14, 0, 0, tzinfo=timezone.utc),
        )
    sa_conn.commit()
    assert ok is False


def test_cult_lateral_is_a_valid_sweep_source(sa_conn):
    """The shared-cache plan §5 lateral flow: upsert_sweep_opportunity must accept
    sweep_source='cult_lateral' (mapped onto the 057-allowed 'auto_mention' origin)."""
    from sable_platform.relay.db import upsert_sweep_opportunity

    sa_conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES ('tig','tig')"))
    sa_conn.execute(text("INSERT INTO relay_clients (org_id) VALUES ('tig')"))
    sa_conn.commit()
    _write(sa_conn, [_raw("611")])
    tid = sa_conn.execute(text("SELECT id FROM relay_tweets WHERE x_id='611'")).fetchone()[0]
    with immediate_txn(sa_conn):
        oid = upsert_sweep_opportunity(
            sa_conn, org_id="tig", tweet_id=int(tid), sweep_source="cult_lateral",
            note="lateral by @alice",
        )
    sa_conn.commit()
    row = sa_conn.execute(
        text("SELECT sweep_source, origin FROM relay_reply_opportunities WHERE id=:i"),
        {"i": oid},
    ).fetchone()
    assert row[0] == "cult_lateral" and row[1] == "auto_mention"
