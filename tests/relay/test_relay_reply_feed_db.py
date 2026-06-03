"""Migration 062 — reply-opportunity feed CRUD tests (sable_platform.relay.db).

Exercises the P0 feed helpers against the in-memory ``sa_conn`` schema:
  * sentinel-member idempotency (one __sweep__ member per org, keeps flagger_id
    NOT NULL on auto rows);
  * application-level dedup upsert (NO UNIQUE(org_id,tweet_id) — re-surface
    updates score, never extends expiry, never revives a terminal row);
  * the §4 due-orgs state machine incl. the one-click-one-sweep property;
  * relay_tweets read-through cache TTL;
  * the per-operator feed exclusion of dismissed / currently-snoozed rows +
    handled depression + score ordering + no-cost-column rule;
  * the two thumbs;
  * GC (expire / purge / feedback retention).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.txn import immediate_txn


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(conn, *, org_id="orgA"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled, config) VALUES (:o, 1, '{}')"),
        {"o": org_id},
    )


def _seed_tweet(conn, x_id: str, *, handle="someone", text_body="hello") -> int:
    with immediate_txn(conn):
        return relay_db.upsert_relay_tweet(
            conn, x_id=x_id, x_author_handle=handle, text_body=text_body
        )


# ==========================================================================
# Sentinel member idempotency
# ==========================================================================
def test_sweep_sentinel_idempotent_per_org(sa_conn):
    _seed(sa_conn, org_id="orgA")
    _seed(sa_conn, org_id="orgB")
    sa_conn.commit()
    with immediate_txn(sa_conn):
        a1 = relay_db.get_or_create_sweep_sentinel(sa_conn, "orgA")
        a2 = relay_db.get_or_create_sweep_sentinel(sa_conn, "orgA")
        b1 = relay_db.get_or_create_sweep_sentinel(sa_conn, "orgB")
    assert a1 == a2  # idempotent within an org
    assert a1 != b1  # one sentinel PER org
    # The sentinel carries a deterministic x identity.
    euid = sa_conn.execute(
        text("SELECT external_user_id FROM relay_member_identities WHERE member_id = :m"),
        {"m": a1},
    ).fetchone()[0]
    assert euid == "__sweep__::orgA"
    # Exactly one __sweep__ member per org (no duplicates after repeat calls).
    n = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_member_identities WHERE handle = '__sweep__'")
    ).fetchone()[0]
    assert n == 2


# ==========================================================================
# Application-level dedup upsert
# ==========================================================================
def test_upsert_sweep_opportunity_inserts_then_dedups(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    tid = _seed_tweet(sa_conn, "100")

    with immediate_txn(sa_conn):
        oid1 = relay_db.upsert_sweep_opportunity(
            sa_conn, org_id="orgA", tweet_id=tid, sweep_source="mention",
            score=0.5, score_reason="r1", suggested_angle="a1", expiry_hours=36,
        )
    row1 = sa_conn.execute(
        text("SELECT origin, status, score, expires_at, flagger_id, sweep_source "
             "FROM relay_reply_opportunities WHERE id = :i"), {"i": oid1},
    ).fetchone()
    assert row1._mapping["origin"] == "auto_mention"  # mention -> auto_mention
    assert row1._mapping["status"] == "active"
    assert row1._mapping["score"] == 0.5
    assert row1._mapping["sweep_source"] == "mention"
    assert row1._mapping["flagger_id"] is not None  # sentinel keeps it NOT NULL
    first_expiry = row1._mapping["expires_at"]

    # Re-surface the SAME tweet: dedups onto the same row, updates score, does NOT
    # touch expires_at, does NOT insert a second row.
    with immediate_txn(sa_conn):
        oid2 = relay_db.upsert_sweep_opportunity(
            sa_conn, org_id="orgA", tweet_id=tid, sweep_source="mention",
            score=0.9, score_reason="r2", suggested_angle="a2", expiry_hours=999,
        )
    assert oid2 == oid1
    row2 = sa_conn.execute(
        text("SELECT score, score_reason, suggested_angle, expires_at "
             "FROM relay_reply_opportunities WHERE id = :i"), {"i": oid1},
    ).fetchone()
    assert row2._mapping["score"] == 0.9
    assert row2._mapping["score_reason"] == "r2"
    assert row2._mapping["suggested_angle"] == "a2"
    assert row2._mapping["expires_at"] == first_expiry  # NOT extended
    n = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_reply_opportunities WHERE tweet_id = :t"), {"t": tid},
    ).fetchone()[0]
    assert n == 1  # no duplicate


def test_upsert_sweep_operator_submit_maps_to_explicit_command(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    tid = _seed_tweet(sa_conn, "201")
    with immediate_txn(sa_conn):
        oid = relay_db.upsert_sweep_opportunity(
            sa_conn, org_id="orgA", tweet_id=tid, sweep_source="operator_submit",
        )
    origin = sa_conn.execute(
        text("SELECT origin FROM relay_reply_opportunities WHERE id = :i"), {"i": oid},
    ).fetchone()[0]
    assert origin == "explicit_command"


def test_upsert_sweep_terminal_row_not_revived(sa_conn):
    """A handled/expired/dismissed row for the same tweet is NOT revived — a new
    active row is inserted instead (terminal rows are excluded from the dedup
    lookup, so they never flip back to active)."""
    _seed(sa_conn)
    sa_conn.commit()
    tid = _seed_tweet(sa_conn, "300")
    with immediate_txn(sa_conn):
        oid1 = relay_db.upsert_sweep_opportunity(
            sa_conn, org_id="orgA", tweet_id=tid, sweep_source="topic", score=0.4,
        )
        relay_db.mark_opportunity_handled(sa_conn, oid1)
    with immediate_txn(sa_conn):
        oid2 = relay_db.upsert_sweep_opportunity(
            sa_conn, org_id="orgA", tweet_id=tid, sweep_source="topic", score=0.7,
        )
    assert oid2 != oid1
    # The handled row stays handled; the new one is active.
    statuses = {
        r[0]: r[1] for r in sa_conn.execute(
            text("SELECT id, status FROM relay_reply_opportunities WHERE tweet_id = :t"),
            {"t": tid},
        ).fetchall()
    }
    assert statuses[oid1] == "handled"
    assert statuses[oid2] == "active"


def test_upsert_sweep_rejects_unknown_source(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    tid = _seed_tweet(sa_conn, "400")
    import pytest
    with immediate_txn(sa_conn):
        with pytest.raises(ValueError):
            relay_db.upsert_sweep_opportunity(
                sa_conn, org_id="orgA", tweet_id=tid, sweep_source="garbage",
            )


# ==========================================================================
# Per-operator feed
# ==========================================================================
def test_feed_orders_by_score_and_excludes_other_orgs(sa_conn):
    _seed(sa_conn, org_id="orgA")
    _seed(sa_conn, org_id="orgB")
    sa_conn.commit()
    ta = _seed_tweet(sa_conn, "a-low")
    tb = _seed_tweet(sa_conn, "a-high")
    tc = _seed_tweet(sa_conn, "b-only")
    with immediate_txn(sa_conn):
        relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=ta, sweep_source="topic", score=0.2)
        relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=tb, sweep_source="topic", score=0.9)
        relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgB", tweet_id=tc, sweep_source="topic", score=0.99)
    feed = relay_db.list_feed_opportunities(sa_conn, "orgA", "@op")
    assert [f["tweet_id"] for f in feed] == [tb, ta]  # score DESC, org-filtered
    # cost is NEVER in the payload.
    for f in feed:
        assert "cost_usd" not in f and "cost" not in f


def test_feed_excludes_dismissed_and_snoozed(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    t1 = _seed_tweet(sa_conn, "f1")
    t2 = _seed_tweet(sa_conn, "f2")
    t3 = _seed_tweet(sa_conn, "f3")
    with immediate_txn(sa_conn):
        o1 = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t1, sweep_source="topic", score=0.5)
        o2 = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t2, sweep_source="topic", score=0.5)
        o3 = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t3, sweep_source="topic", score=0.5)
        relay_db.set_operator_opportunity_state(
            sa_conn, opportunity_id=o1, operator_handle="@op", state="dismissed",
        )
        future = _iso(datetime.now(timezone.utc) + timedelta(hours=2))
        relay_db.set_operator_opportunity_state(
            sa_conn, opportunity_id=o2, operator_handle="@op", state="snoozed", snooze_until=future,
        )
    feed_ids = {f["id"] for f in relay_db.list_feed_opportunities(sa_conn, "orgA", "@op")}
    assert feed_ids == {o3}  # o1 dismissed, o2 snoozed-in-future -> hidden
    # A DIFFERENT operator still sees all three (per-operator state).
    feed_other = {f["id"] for f in relay_db.list_feed_opportunities(sa_conn, "orgA", "@other")}
    assert feed_other == {o1, o2, o3}


def test_feed_snooze_in_past_reappears(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    t1 = _seed_tweet(sa_conn, "s1")
    with immediate_txn(sa_conn):
        o1 = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t1, sweep_source="topic", score=0.5)
        past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
        relay_db.set_operator_opportunity_state(
            sa_conn, opportunity_id=o1, operator_handle="@op", state="snoozed", snooze_until=past,
        )
    feed_ids = {f["id"] for f in relay_db.list_feed_opportunities(sa_conn, "orgA", "@op")}
    assert o1 in feed_ids  # snooze expired -> back in the feed


def test_feed_depresses_handled_to_bottom(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    t_high = _seed_tweet(sa_conn, "h-high")
    t_handled = _seed_tweet(sa_conn, "h-handled")
    with immediate_txn(sa_conn):
        relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t_high, sweep_source="topic", score=0.1)
        o_handled = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t_handled, sweep_source="topic", score=0.99)
        relay_db.mark_opportunity_handled(sa_conn, o_handled)
    feed = relay_db.list_feed_opportunities(sa_conn, "orgA", "@op")
    # The handled (score 0.99) row sinks below the active (score 0.1) row.
    assert feed[-1]["id"] == o_handled
    assert feed[0]["tweet_id"] == t_high


# ==========================================================================
# Thumbs
# ==========================================================================
def test_record_opportunity_and_suggestion_thumbs(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    t1 = _seed_tweet(sa_conn, "thumb1")
    with immediate_txn(sa_conn):
        oid = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t1, sweep_source="topic", score=0.5)
        # opportunity thumb (suggestion_id NULL)
        relay_db.record_opportunity_feedback(
            sa_conn, opportunity_id=oid, rater_handle="@op", rater_role="operator", thumb=1,
        )
    # seed a reply_suggestion so the suggestion-thumb FK holds
    sa_conn.execute(
        text("INSERT INTO reply_suggestions (id, operator_handle, org_id, source_tweet_id) "
             "VALUES ('sug1', '@op', 'orgA', '1')")
    )
    sa_conn.commit()
    with immediate_txn(sa_conn):
        relay_db.record_opportunity_feedback(
            sa_conn, opportunity_id=oid, suggestion_id="sug1",
            rater_handle="bharat", rater_role="client_ops", thumb=-1,
        )
    rows = sa_conn.execute(
        text("SELECT suggestion_id, rater_role, thumb FROM relay_opportunity_feedback "
             "WHERE opportunity_id = :o ORDER BY id"), {"o": oid},
    ).fetchall()
    assert (rows[0]._mapping["suggestion_id"], rows[0]._mapping["thumb"]) == (None, 1)
    assert (rows[1]._mapping["suggestion_id"], rows[1]._mapping["rater_role"], rows[1]._mapping["thumb"]) == ("sug1", "client_ops", -1)


def test_record_feedback_rejects_bad_thumb_and_role(sa_conn):
    import pytest
    _seed(sa_conn)
    sa_conn.commit()
    t1 = _seed_tweet(sa_conn, "badthumb")
    with immediate_txn(sa_conn):
        oid = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t1, sweep_source="topic")
    with immediate_txn(sa_conn):
        with pytest.raises(ValueError):
            relay_db.record_opportunity_feedback(
                sa_conn, opportunity_id=oid, rater_handle="@op", rater_role="operator", thumb=0,
            )
    with immediate_txn(sa_conn):
        with pytest.raises(ValueError):
            relay_db.record_opportunity_feedback(
                sa_conn, opportunity_id=oid, rater_handle="@op", rater_role="nobody", thumb=1,
            )


# ==========================================================================
# sweep_config CRUD + the §4 state machine
# ==========================================================================
def test_sweep_config_upsert_and_partial_update(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        relay_db.upsert_sweep_config(
            sa_conn, org_id="orgA", mention_handles='["@tig"]', enabled=1,
        )
    cfg = relay_db.get_sweep_config(sa_conn, "orgA")
    assert cfg["mention_handles"] == '["@tig"]'
    assert cfg["enabled"] == 1
    assert cfg["topic_queries"] == "[]"  # default applied
    assert cfg["expiry_hours"] == 36     # default applied
    # Partial update leaves untouched fields alone.
    with immediate_txn(sa_conn):
        relay_db.upsert_sweep_config(sa_conn, org_id="orgA", topic_queries='["swarm"]')
    cfg2 = relay_db.get_sweep_config(sa_conn, "orgA")
    assert cfg2["mention_handles"] == '["@tig"]'  # unchanged
    assert cfg2["topic_queries"] == '["swarm"]'
    assert cfg2["enabled"] == 1  # unchanged


def test_due_orgs_requires_enabled_and_heartbeat(sa_conn):
    _seed(sa_conn, org_id="orgEnabled")
    _seed(sa_conn, org_id="orgDisabled")
    _seed(sa_conn, org_id="orgNoHeartbeat")
    sa_conn.commit()
    now = _now_iso()
    with immediate_txn(sa_conn):
        relay_db.upsert_sweep_config(sa_conn, org_id="orgEnabled", enabled=1)
        relay_db.upsert_sweep_config(sa_conn, org_id="orgDisabled", enabled=0)
        relay_db.upsert_sweep_config(sa_conn, org_id="orgNoHeartbeat", enabled=1)
        relay_db.write_operator_heartbeat(sa_conn, org_id="orgEnabled", operator_handle="@op", now=now)
        relay_db.write_operator_heartbeat(sa_conn, org_id="orgDisabled", operator_handle="@op", now=now)
        # orgNoHeartbeat: enabled but no heartbeat
    due = relay_db.list_due_sweep_orgs(sa_conn, now=now)
    assert due == ["orgEnabled"]


def test_due_orgs_stale_heartbeat_excluded(sa_conn):
    _seed(sa_conn, org_id="orgStale")
    sa_conn.commit()
    now = _now_iso()
    stale = _iso(datetime.now(timezone.utc) - timedelta(hours=5))
    with immediate_txn(sa_conn):
        relay_db.upsert_sweep_config(sa_conn, org_id="orgStale", enabled=1)
        relay_db.write_operator_heartbeat(sa_conn, org_id="orgStale", operator_handle="@op", now=stale)
    assert relay_db.list_due_sweep_orgs(sa_conn, now=now, heartbeat_within_hours=2) == []


def test_due_orgs_one_click_one_sweep(sa_conn):
    """The load-bearing §4 property: one 'sweep now' triggers EXACTLY one extra
    sweep — stamping last_sweep_at at completion auto-consumes the request."""
    _seed(sa_conn, org_id="orgX")
    sa_conn.commit()
    now = _now_iso()
    recent = _iso(datetime.now(timezone.utc) - timedelta(minutes=10))  # < 1h ago
    with immediate_txn(sa_conn):
        relay_db.upsert_sweep_config(sa_conn, org_id="orgX", enabled=1)
        relay_db.write_operator_heartbeat(sa_conn, org_id="orgX", operator_handle="@op", now=now)
        relay_db.mark_sweep_completed(sa_conn, "orgX", now=recent)
    # Just completed 10 min ago, no request -> NOT due (hourly cadence not reached).
    assert relay_db.list_due_sweep_orgs(sa_conn, now=now) == []
    # Operator clicks "sweep now" (enqueue).
    request_ts = _iso(datetime.now(timezone.utc) - timedelta(minutes=5))
    with immediate_txn(sa_conn):
        relay_db.mark_sweep_requested(sa_conn, "orgX", now=request_ts)
    # Now due (request > last_sweep_at).
    assert relay_db.list_due_sweep_orgs(sa_conn, now=now) == ["orgX"]
    # The sweep runs and completes -> stamps last_sweep_at = now (>= request_ts).
    with immediate_txn(sa_conn):
        relay_db.mark_sweep_completed(sa_conn, "orgX", now=now)
    # The request is auto-consumed -> NOT due again (exactly one extra sweep).
    assert relay_db.list_due_sweep_orgs(sa_conn, now=now) == []


def test_due_orgs_hourly_cadence(sa_conn):
    _seed(sa_conn, org_id="orgHourly")
    sa_conn.commit()
    now = _now_iso()
    over_an_hour = _iso(datetime.now(timezone.utc) - timedelta(hours=2))
    with immediate_txn(sa_conn):
        relay_db.upsert_sweep_config(sa_conn, org_id="orgHourly", enabled=1)
        relay_db.write_operator_heartbeat(sa_conn, org_id="orgHourly", operator_handle="@op", now=now)
        relay_db.mark_sweep_completed(sa_conn, "orgHourly", now=over_an_hour)
    # Last sweep > 1h ago -> due on the hourly cadence even with no request.
    assert relay_db.list_due_sweep_orgs(sa_conn, now=now) == ["orgHourly"]


def test_due_orgs_never_swept_is_due(sa_conn):
    _seed(sa_conn, org_id="orgFresh")
    sa_conn.commit()
    now = _now_iso()
    with immediate_txn(sa_conn):
        relay_db.upsert_sweep_config(sa_conn, org_id="orgFresh", enabled=1)
        relay_db.write_operator_heartbeat(sa_conn, org_id="orgFresh", operator_handle="@op", now=now)
    # last_sweep_at IS NULL -> due.
    assert relay_db.list_due_sweep_orgs(sa_conn, now=now) == ["orgFresh"]


# ==========================================================================
# relay_tweets read-through cache TTL
# ==========================================================================
def test_tweet_cache_hit_within_ttl_and_miss_when_stale(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        relay_db.upsert_relay_tweet(
            sa_conn, x_id="cache1", x_author_handle="a", text_body="fresh",
            engagement_json='{"likes":3}', lang="en", author_followers=1000,
        )
    hit = relay_db.get_cached_relay_tweet(sa_conn, "cache1")
    assert hit is not None
    assert hit["engagement_json"] == '{"likes":3}'
    assert hit["lang"] == "en"
    assert hit["author_followers"] == 1000
    # Force the fetched_at to be stale (> 6h).
    stale = _iso(datetime.now(timezone.utc) - timedelta(hours=7))
    sa_conn.execute(
        text("UPDATE relay_tweets SET fetched_at = :s WHERE x_id = 'cache1'"), {"s": stale}
    )
    sa_conn.commit()
    assert relay_db.get_cached_relay_tweet(sa_conn, "cache1") is None  # stale -> miss
    assert relay_db.get_cached_relay_tweet(sa_conn, "nonexistent") is None


def test_upsert_relay_tweet_idempotent_refresh(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        id1 = relay_db.upsert_relay_tweet(sa_conn, x_id="re1", x_author_handle="a", author_followers=10)
    with immediate_txn(sa_conn):
        id2 = relay_db.upsert_relay_tweet(sa_conn, x_id="re1", x_author_handle="a", author_followers=20)
    assert id1 == id2
    fol = sa_conn.execute(
        text("SELECT author_followers FROM relay_tweets WHERE x_id='re1'")
    ).fetchone()[0]
    assert fol == 20


# ==========================================================================
# Heartbeat
# ==========================================================================
def test_heartbeat_recent_and_stale(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    now = _now_iso()
    with immediate_txn(sa_conn):
        relay_db.write_operator_heartbeat(sa_conn, org_id="orgA", operator_handle="@op", now=now)
    assert relay_db.has_recent_heartbeat(sa_conn, "orgA", within_hours=2, now=now) is True
    far = _iso(datetime.now(timezone.utc) + timedelta(hours=5))
    assert relay_db.has_recent_heartbeat(sa_conn, "orgA", within_hours=2, now=far) is False


# ==========================================================================
# GC
# ==========================================================================
def test_gc_expires_active_past_expiry(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    t1 = _seed_tweet(sa_conn, "gc1")
    with immediate_txn(sa_conn):
        oid = relay_db.upsert_sweep_opportunity(
            sa_conn, org_id="orgA", tweet_id=t1, sweep_source="topic", expiry_hours=1,
        )
    # Push expiry into the past.
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    sa_conn.execute(
        text("UPDATE relay_reply_opportunities SET expires_at = :p WHERE id = :i"),
        {"p": past, "i": oid},
    )
    sa_conn.commit()
    with immediate_txn(sa_conn):
        counts = relay_db.gc_expired_opportunities(sa_conn)
    assert counts["expired"] == 1
    status = sa_conn.execute(
        text("SELECT status FROM relay_reply_opportunities WHERE id = :i"), {"i": oid}
    ).fetchone()[0]
    assert status == "expired"


def test_gc_purges_7d_past_expiry_without_feedback(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    t1 = _seed_tweet(sa_conn, "gc-purge")
    with immediate_txn(sa_conn):
        oid = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t1, sweep_source="topic")
        relay_db.set_operator_opportunity_state(
            sa_conn, opportunity_id=oid, operator_handle="@op", state="dismissed",
        )
    old_expiry = _iso(datetime.now(timezone.utc) - timedelta(days=8))
    sa_conn.execute(
        text("UPDATE relay_reply_opportunities SET expires_at = :e WHERE id = :i"),
        {"e": old_expiry, "i": oid},
    )
    sa_conn.commit()
    with immediate_txn(sa_conn):
        counts = relay_db.gc_expired_opportunities(sa_conn)
    assert counts["purged"] == 1
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_reply_opportunities WHERE id = :i"), {"i": oid}
    ).fetchone()[0] == 0
    # The per-operator state was FK-safe-deleted too.
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_opportunity_operator_state WHERE opportunity_id = :i"),
        {"i": oid},
    ).fetchone()[0] == 0


def test_gc_keeps_opportunity_with_recent_feedback(sa_conn):
    """An opportunity 7d past expiry but still carrying <90d feedback is RETAINED
    (the learning corpus is kept 90 days)."""
    _seed(sa_conn)
    sa_conn.commit()
    t1 = _seed_tweet(sa_conn, "gc-keep")
    with immediate_txn(sa_conn):
        oid = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t1, sweep_source="topic")
        relay_db.record_opportunity_feedback(
            sa_conn, opportunity_id=oid, rater_handle="@op", rater_role="operator", thumb=1,
        )
    old_expiry = _iso(datetime.now(timezone.utc) - timedelta(days=8))
    sa_conn.execute(
        text("UPDATE relay_reply_opportunities SET expires_at = :e WHERE id = :i"),
        {"e": old_expiry, "i": oid},
    )
    sa_conn.commit()
    with immediate_txn(sa_conn):
        counts = relay_db.gc_expired_opportunities(sa_conn)
    assert counts["purged"] == 0  # retained — feedback < 90d
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_reply_opportunities WHERE id = :i"), {"i": oid}
    ).fetchone()[0] == 1


def test_gc_prunes_feedback_older_than_90d(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    t1 = _seed_tweet(sa_conn, "gc-fb")
    with immediate_txn(sa_conn):
        oid = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t1, sweep_source="topic")
        fid = relay_db.record_opportunity_feedback(
            sa_conn, opportunity_id=oid, rater_handle="@op", rater_role="operator", thumb=1,
        )
    old = _iso(datetime.now(timezone.utc) - timedelta(days=91))
    sa_conn.execute(
        text("UPDATE relay_opportunity_feedback SET created_at = :c WHERE id = :i"),
        {"c": old, "i": fid},
    )
    sa_conn.commit()
    with immediate_txn(sa_conn):
        counts = relay_db.gc_expired_opportunities(sa_conn)
    assert counts["feedback_pruned"] == 1
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_opportunity_feedback WHERE id = :i"), {"i": fid}
    ).fetchone()[0] == 0
