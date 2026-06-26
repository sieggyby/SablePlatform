"""Deck posted->outcome capture + rollup (no migration).

Covers: parse_tweet_id normalization (URL/bare/garbage/None); mark_posted storing a bare tweet id
from a posted URL (best-effort -> NULL on garbage); and get_deck_posted_performance — the matured-only
audience-outcome rollup (mean 24h engagement = likes+RT+replies over posts with a 24h/'ok'
relay_tweet_snapshots row, maturing posts never counted as zero, org-scoped, per-kind).
"""
from __future__ import annotations

from sable_platform.db import content_deck as cd
from sable_platform.db import content_publish as cp
from sable_platform.relay.bot.txn import immediate_txn
from tests.conftest import make_test_conn

_NOW = "2026-06-26T12:00:00Z"


def test_parse_tweet_id():
    assert cp.parse_tweet_id("https://x.com/tigfoundation/status/1812345678901234567") == "1812345678901234567"
    assert cp.parse_tweet_id("https://twitter.com/x/status/1812345678901234567?s=20") == "1812345678901234567"
    assert cp.parse_tweet_id("1812345678901234567") == "1812345678901234567"
    assert cp.parse_tweet_id("  1812345678901234567  ") == "1812345678901234567"
    assert cp.parse_tweet_id("not a tweet url") is None
    assert cp.parse_tweet_id("") is None
    assert cp.parse_tweet_id(None) is None
    # a /photo/ index or other stray number must NOT win over the /status/ id
    assert cp.parse_tweet_id("https://x.com/x/status/1812345678901234567/photo/1") == "1812345678901234567"
    # STRICT (Codex) — never store a TRUNCATED or WRONG id:
    assert cp.parse_tweet_id("https://x.com/u/status/12345abc") is None          # letters after digits
    assert cp.parse_tweet_id("https://x.com/u/status/" + "1" * 26) is None        # 26 digits — over range, not truncated
    assert cp.parse_tweet_id("https://example.com/status/12345678") is None       # non-X host
    assert cp.parse_tweet_id("notstatus/12345678") is None                        # not a real /status/ segment
    assert cp.parse_tweet_id("junk status/12345678") is None
    assert cp.parse_tweet_id("1234") is None                                      # too short for a bare id


def _ensure_org(conn):
    from sqlalchemy import text
    conn.execute(text("INSERT OR IGNORE INTO orgs (org_id, display_name) VALUES ('tig', 'TIG')"))


def _seed(conn, *, kind, posted_ref, snapshot=None):
    """Insert a candidate + a POSTED publish job; optionally a 24h/'ok' snapshot for the posted_ref."""
    sa = getattr(conn, "_conn", conn)
    with immediate_txn(sa):
        _ensure_org(conn)
        cid = cd.upsert_candidate(
            conn, org_id="tig", kind=kind, source="test", target_handle="@tigfoundation",
            payload_json='{"text":"x"}',
        )
        conn.execute(
            __import__("sqlalchemy").text(
                "INSERT INTO content_publish_jobs "
                "(candidate_id, org_id, target_handle, release_state, publish_at, posted_ref, "
                " attempt_count, created_at, updated_at) "
                "VALUES (:cid, 'tig', '@tigfoundation', 'posted', :pa, :ref, 0, :ca, :ua)"
            ),
            {"cid": cid, "pa": _NOW, "ref": posted_ref, "ca": _NOW, "ua": _NOW},
        )
        if snapshot is not None:
            conn.execute(
                __import__("sqlalchemy").text(
                    "INSERT INTO relay_tweet_snapshots "
                    "(tweet_x_id, target_age_hours, taken_at, age_hours, likes, retweets, replies, status) "
                    "VALUES (:xid, 24, :ta, 24.0, :l, :rt, :rp, 'ok')"
                ),
                {"xid": posted_ref, "ta": _NOW, "l": snapshot[0], "rt": snapshot[1], "rp": snapshot[2]},
            )
    return cid


def test_mark_posted_normalizes_posted_ref_to_bare_id():
    from sqlalchemy import text
    conn = make_test_conn()
    sa = getattr(conn, "_conn", conn)
    with immediate_txn(sa):
        _ensure_org(conn)
        # seed a 'scheduled' candidate + a 'due' job directly (mark_posted requires candidate='scheduled')
        conn.execute(text(
            "INSERT INTO content_candidates (id, org_id, kind, status, target_handle, payload_json, source, created_at) "
            "VALUES (50, 'tig', 'tweet', 'scheduled', '@tigfoundation', '{}', 't', :n)"), {"n": _NOW})
        conn.execute(text(
            "INSERT INTO content_publish_jobs (id, candidate_id, org_id, target_handle, release_state, publish_at, "
            " attempt_count, created_at, updated_at) "
            "VALUES (60, 50, 'tig', '@tigfoundation', 'due', '2000-01-01T00:00:00Z', 0, :n, :n)"), {"n": _NOW})
    with immediate_txn(sa):
        ok = cp.mark_posted(
            conn, job_id=60, org_id="tig", authorized_target_handle="@tigfoundation",
            posted_ref="https://x.com/tigfoundation/status/1899000000000000001",
        )
    assert ok
    row = conn.execute(text("SELECT posted_ref FROM content_publish_jobs WHERE id = 60")).fetchone()
    assert row[0] == "1899000000000000001"  # URL normalized to the bare id


def test_posted_performance_rollup_matured_only():
    conn = make_test_conn()
    # meme A: matured, engagement 10+5+2=17 ; meme B: matured, 0 ; tweet C: MATURING (no snapshot)
    _seed(conn, kind="meme", posted_ref="1001", snapshot=(10, 5, 2))
    _seed(conn, kind="meme", posted_ref="1002", snapshot=(0, 0, 0))
    _seed(conn, kind="tweet", posted_ref="1003", snapshot=None)

    perf = cp.get_deck_posted_performance(conn, "tig")
    assert perf["posted_count"] == 3
    assert perf["measured_count"] == 2          # only the two with a 24h/ok row
    assert perf["maturing_count"] == 1          # the un-snapshotted tweet — never counted as zero
    assert perf["avg_engagement"] == (17 + 0) / 2  # mean over matured only
    by = {k["kind"]: k for k in perf["by_kind"]}
    assert by["meme"]["measured"] == 2 and by["meme"]["avg_engagement"] == 17 / 2
    assert by["tweet"]["measured"] == 0 and by["tweet"]["avg_engagement"] is None


def test_posted_performance_dedups_shared_posted_ref():
    """Two posted jobs marked with the SAME tweet (operator dup-entry) count the TWEET once —
    measured/posted are not inflated."""
    from sqlalchemy import text
    conn = make_test_conn()
    _seed(conn, kind="meme", posted_ref="2001", snapshot=(20, 0, 0))
    sa = getattr(conn, "_conn", conn)
    with immediate_txn(sa):
        conn.execute(text(
            "INSERT INTO content_candidates (id, org_id, kind, status, target_handle, payload_json, source, created_at) "
            "VALUES (2, 'tig', 'meme', 'posted', '@tigfoundation', '{}', 't', :n)"), {"n": _NOW})
        conn.execute(text(
            "INSERT INTO content_publish_jobs (id, candidate_id, org_id, target_handle, release_state, publish_at, "
            " posted_ref, attempt_count, created_at, updated_at) "
            "VALUES (200, 2, 'tig', '@tigfoundation', 'posted', '2000-01-01T00:00:00Z', '2001', 0, :n, :n)"), {"n": _NOW})
    perf = cp.get_deck_posted_performance(conn, "tig")
    assert perf["posted_count"] == 1 and perf["measured_count"] == 1  # one DISTINCT tweet, not two
    assert perf["avg_engagement"] == 20


def test_posted_performance_deleted_tweet_is_terminal_not_maturing():
    """A posted tweet that 404'd (a 'deleted' 24h snapshot) is TERMINAL — never counted as maturing
    forever, never as zero engagement."""
    from sqlalchemy import text
    conn = make_test_conn()
    sa = getattr(conn, "_conn", conn)
    with immediate_txn(sa):
        _ensure_org(conn)
        conn.execute(text(
            "INSERT INTO content_candidates (id, org_id, kind, status, target_handle, payload_json, source, created_at) "
            "VALUES (3, 'tig', 'tweet', 'posted', '@tigfoundation', '{}', 't', :n)"), {"n": _NOW})
        conn.execute(text(
            "INSERT INTO content_publish_jobs (id, candidate_id, org_id, target_handle, release_state, publish_at, "
            " posted_ref, attempt_count, created_at, updated_at) "
            "VALUES (300, 3, 'tig', '@tigfoundation', 'posted', '2000-01-01T00:00:00Z', '3001', 0, :n, :n)"), {"n": _NOW})
        conn.execute(text(
            "INSERT INTO relay_tweet_snapshots (tweet_x_id, target_age_hours, taken_at, age_hours, status) "
            "VALUES ('3001', 24, :n, 24.0, 'deleted')"), {"n": _NOW})
    perf = cp.get_deck_posted_performance(conn, "tig")
    assert perf["posted_count"] == 1 and perf["measured_count"] == 0
    assert perf["maturing_count"] == 0  # the gone tweet is terminal, NOT maturing


def test_posted_performance_prefers_ok_over_duplicate_deleted_row():
    """Determinism (Codex): with no UNIQUE on (tweet_x_id, target), if a tweet has BOTH a 'deleted'
    and an 'ok' 24h row, the rollup deterministically prefers the 'ok' measurement."""
    from sqlalchemy import text
    conn = make_test_conn()
    _seed(conn, kind="meme", posted_ref="4001", snapshot=(30, 0, 0))  # writes an 'ok' row
    sa = getattr(conn, "_conn", conn)
    with immediate_txn(sa):  # add a stray duplicate 'deleted' row for the same tweet+age
        conn.execute(text(
            "INSERT INTO relay_tweet_snapshots (tweet_x_id, target_age_hours, taken_at, age_hours, status) "
            "VALUES ('4001', 24, :n, 24.0, 'deleted')"), {"n": _NOW})
    perf = cp.get_deck_posted_performance(conn, "tig")
    assert perf["measured_count"] == 1 and perf["avg_engagement"] == 30  # 'ok' wins, not the deleted


def test_posted_performance_empty_org():
    conn = make_test_conn()
    perf = cp.get_deck_posted_performance(conn, "nobody")
    assert perf == {"posted_count": 0, "measured_count": 0, "avg_engagement": None, "maturing_count": 0, "by_kind": []}
