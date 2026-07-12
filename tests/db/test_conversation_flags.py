"""mig 086 — Conversation Watcher flag CRUD (sable_platform.db.conversation_flags).

Exercises the substrate the watcher depends on: insert with app-level per-channel dedupe,
the brand_risk-first deliverable queue, the single-flight deliver transition, feedback
termination (the precision-gate signal), and expiry GC.
"""
from __future__ import annotations

from sqlalchemy import text

from sable_platform.db import conversation_flags as cf
from sable_platform.relay.bot.txn import immediate_txn


def _seed(conn, *orgs):
    for o in orgs or ("tig",):
        conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": o})
    conn.commit()


def _insert(conn, *, org="tig", channel="c1", anchor="m1", kind="opportunity",
            score=72.0, ws="2026-07-12T00:00:00Z", we="2026-07-12T00:03:00Z",
            now="2026-07-12T00:03:05Z", cooldown=45, expires=None, sig='{"burst":4.0}'):
    with immediate_txn(conn):
        return cf.insert_flag(
            conn, org_id=org, platform="discord", space_id="g1", channel_id=channel,
            anchor_message_id=anchor, window_start=ws, window_end=we, score=score,
            signals_json=sig, reason="5 people, 4x baseline", kind=kind,
            cooldown_minutes=cooldown, expires_at=expires, now=now,
        )


def test_insert_and_list(sa_conn):
    _seed(sa_conn)
    fid = _insert(sa_conn)
    assert fid is not None and fid > 0
    rows = cf.list_active_flags(sa_conn, "tig")
    assert len(rows) == 1
    r = rows[0]
    assert r["channel_id"] == "c1" and r["status"] == "active" and r["kind"] == "opportunity"
    assert r["anchor_message_id"] == "m1" and abs(r["score"] - 72.0) < 1e-9
    assert r["signals_json"] == '{"burst":4.0}'


def test_cooldown_dedupe_same_channel(sa_conn):
    _seed(sa_conn)
    first = _insert(sa_conn, anchor="m1", now="2026-07-12T00:03:05Z")
    # 20 min later, same channel+kind, still inside the 45-min cooldown -> suppressed.
    second = _insert(sa_conn, anchor="m2", now="2026-07-12T00:23:05Z")
    assert first is not None and second is None
    assert len(cf.list_active_flags(sa_conn, "tig")) == 1


def test_cooldown_lapses_after_window(sa_conn):
    _seed(sa_conn)
    _insert(sa_conn, anchor="m1", now="2026-07-12T00:03:05Z")
    # 50 min later -> past the 45-min cooldown -> a new flag is allowed.
    later = _insert(sa_conn, anchor="m2", now="2026-07-12T00:53:05Z")
    assert later is not None
    assert len(cf.list_active_flags(sa_conn, "tig")) == 2


def test_brand_risk_not_deduped_against_opportunity(sa_conn):
    _seed(sa_conn)
    # Different kind in the same channel/window is a separate cooldown lane.
    opp = _insert(sa_conn, kind="opportunity", anchor="m1")
    risk = _insert(sa_conn, kind="brand_risk", anchor="m2")
    assert opp is not None and risk is not None


def test_deliverable_queue_is_brand_risk_first(sa_conn):
    _seed(sa_conn)
    # Opportunity created FIRST (older), brand_risk SECOND (newer) — queue must still
    # surface brand_risk ahead of the older opportunity.
    _insert(sa_conn, kind="opportunity", channel="c1", anchor="m1", now="2026-07-12T00:00:10Z")
    _insert(sa_conn, kind="brand_risk", channel="c2", anchor="m2", now="2026-07-12T00:05:10Z")
    q = cf.list_deliverable_flags(sa_conn)
    assert [r["kind"] for r in q] == ["brand_risk", "opportunity"]


def test_mark_delivered_single_flight(sa_conn):
    _seed(sa_conn)
    fid = _insert(sa_conn)
    with immediate_txn(sa_conn):
        assert cf.mark_delivered(sa_conn, fid) is True
    # Second delivery attempt finds no 'active' row -> no double-post.
    with immediate_txn(sa_conn):
        assert cf.mark_delivered(sa_conn, fid) is False
    assert cf.list_active_flags(sa_conn, "tig", status="delivered")[0]["delivered_at"] is not None
    # A delivered flag still suppresses a new flag in-cooldown (not yet adjudicated).
    dup = _insert(sa_conn, anchor="m2", now="2026-07-12T00:10:00Z")
    assert dup is None


def test_feedback_terminates_and_frees_cooldown(sa_conn):
    _seed(sa_conn)
    fid = _insert(sa_conn, now="2026-07-12T00:03:05Z")
    with immediate_txn(sa_conn):
        assert cf.record_feedback(sa_conn, fid, verdict="noise") is True
    # 'noise' is terminal -> a new flag in the same channel is now allowed even in-window.
    again = _insert(sa_conn, anchor="m2", now="2026-07-12T00:10:00Z")
    assert again is not None
    rows = {r["id"]: r for r in cf.list_active_flags(sa_conn, "tig", status=None)}
    assert rows[fid]["status"] == "noise" and rows[fid]["feedback"] == "noise"


def test_feedback_pitched_maps_to_handled(sa_conn):
    _seed(sa_conn)
    fid = _insert(sa_conn)
    with immediate_txn(sa_conn):
        assert cf.record_feedback(sa_conn, fid, verdict="pitched") is True
    r = cf.list_active_flags(sa_conn, "tig", status="handled")[0]
    assert r["id"] == fid and r["feedback"] == "pitched"


def test_gc_expires_past_ttl(sa_conn):
    _seed(sa_conn)
    _insert(sa_conn, anchor="m1", expires="2026-07-12T01:00:00Z", now="2026-07-12T00:03:05Z")
    with immediate_txn(sa_conn):
        n = cf.gc_expired_flags(sa_conn, now="2026-07-12T02:00:00Z")
    assert n == 1
    assert cf.list_active_flags(sa_conn, "tig", status="active") == []
    assert cf.list_active_flags(sa_conn, "tig", status="expired")[0]["anchor_message_id"] == "m1"
