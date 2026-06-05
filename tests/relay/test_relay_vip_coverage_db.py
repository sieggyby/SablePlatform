"""VIP bank + per-operator coverage (the John Fletcher request, plan §5).

Two coupled behaviors layered on the mig-062 feed with NO new migration (the
``relay_opportunity_operator_state`` table is reused for a per-operator
``'replied'`` state):

  * ``mark_opportunity_handled`` is VIP-EXEMPT — a ``vip`` opportunity is never
    team-depressed, so a principal's tweet stays banked (active + prominent) for
    every operator until each replies. NULL-source (legacy) + all other sources
    depress exactly as before.
  * a per-operator ``'replied'`` state removes an opportunity from THAT operator's
    feed only (the feed reader's existing catch-all exclusion), while teammates
    still see it.
"""
from __future__ import annotations

from sqlalchemy import text

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.txn import immediate_txn


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


def _status(conn, oid: int) -> str:
    return conn.execute(
        text("SELECT status FROM relay_reply_opportunities WHERE id = :i"), {"i": oid}
    ).fetchone()[0]


# ==========================================================================
# mark_opportunity_handled VIP exemption
# ==========================================================================
def test_mark_opportunity_handled_exempts_vip_but_handles_others(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    t_vip = _seed_tweet(sa_conn, "vip-1", handle="dr_johnfletcher")
    t_topic = _seed_tweet(sa_conn, "topic-1")
    with immediate_txn(sa_conn):
        o_vip = relay_db.upsert_sweep_opportunity(
            sa_conn, org_id="orgA", tweet_id=t_vip, sweep_source="vip", score=85.0,
        )
        o_topic = relay_db.upsert_sweep_opportunity(
            sa_conn, org_id="orgA", tweet_id=t_topic, sweep_source="topic", score=0.5,
        )
        relay_db.mark_opportunity_handled(sa_conn, o_vip)
        relay_db.mark_opportunity_handled(sa_conn, o_topic)
    # The VIP opp is NOT depressed (banked for the team); the topic opp IS.
    assert _status(sa_conn, o_vip) == "active"
    assert _status(sa_conn, o_topic) == "handled"


def test_mark_opportunity_handled_still_handles_legacy_null_source(sa_conn):
    """A legacy NULL-``sweep_source`` row (old /flag-reply path) must still depress —
    the VIP exemption is null-safe (``sweep_source IS NULL OR <> 'vip'``)."""
    _seed(sa_conn)
    sa_conn.commit()
    tid = _seed_tweet(sa_conn, "legacy-1")
    sentinel = None
    with immediate_txn(sa_conn):
        sentinel = relay_db.get_or_create_sweep_sentinel(sa_conn, "orgA")
        oid = sa_conn.execute(
            text(
                "INSERT INTO relay_reply_opportunities "
                "(org_id, tweet_id, flagger_id, origin, status, sweep_source) "
                "VALUES (:o, :t, :f, 'auto_mention', 'active', NULL) RETURNING id"
            ),
            {"o": "orgA", "t": tid, "f": sentinel},
        ).fetchone()[0]
        relay_db.mark_opportunity_handled(sa_conn, oid)
    assert _status(sa_conn, oid) == "handled"


# ==========================================================================
# per-operator 'replied' state
# ==========================================================================
def test_set_operator_state_accepts_replied(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    tid = _seed_tweet(sa_conn, "rep-1")
    with immediate_txn(sa_conn):
        oid = relay_db.upsert_sweep_opportunity(
            sa_conn, org_id="orgA", tweet_id=tid, sweep_source="vip", score=85.0,
        )
        relay_db.set_operator_opportunity_state(
            sa_conn, opportunity_id=oid, operator_handle="@arf", state="replied",
        )
    state = sa_conn.execute(
        text(
            "SELECT state FROM relay_opportunity_operator_state "
            "WHERE opportunity_id = :i AND operator_handle = '@arf'"
        ),
        {"i": oid},
    ).fetchone()[0]
    assert state == "replied"


def test_set_operator_state_rejects_unknown(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    tid = _seed_tweet(sa_conn, "rep-bad")
    with immediate_txn(sa_conn):
        oid = relay_db.upsert_sweep_opportunity(
            sa_conn, org_id="orgA", tweet_id=tid, sweep_source="topic", score=0.5,
        )
        try:
            relay_db.set_operator_opportunity_state(
                sa_conn, opportunity_id=oid, operator_handle="@arf", state="bogus",
            )
            raised = False
        except ValueError:
            raised = True
    assert raised


def test_feed_excludes_replied_per_operator(sa_conn):
    """A 'replied' opportunity drops out of THAT operator's feed but stays for others."""
    _seed(sa_conn)
    sa_conn.commit()
    t1 = _seed_tweet(sa_conn, "c1")
    t2 = _seed_tweet(sa_conn, "c2")
    with immediate_txn(sa_conn):
        o1 = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t1, sweep_source="topic", score=0.5)
        o2 = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t2, sweep_source="topic", score=0.5)
        relay_db.set_operator_opportunity_state(
            sa_conn, opportunity_id=o1, operator_handle="@arf", state="replied",
        )
    arf = {f["id"] for f in relay_db.list_feed_opportunities(sa_conn, "orgA", "@arf")}
    assert arf == {o2}  # o1 replied -> gone from @arf's feed
    ben = {f["id"] for f in relay_db.list_feed_opportunities(sa_conn, "orgA", "@ben")}
    assert ben == {o1, o2}  # @ben hasn't replied -> still sees both


# ==========================================================================
# The headline: a VIP tweet is banked until EACH operator replies
# ==========================================================================
def test_vip_banked_until_each_operator_replies(sa_conn):
    """Simulate the generate flow on a VIP opportunity: @arf replies (per-op
    'replied' + the VIP-exempt team-handle). It leaves @arf's feed but stays
    ACTIVE + visible for @ben — the bank holds until @ben replies too."""
    _seed(sa_conn)
    sa_conn.commit()
    t_vip = _seed_tweet(sa_conn, "bank-vip", handle="dr_johnfletcher")
    with immediate_txn(sa_conn):
        o_vip = relay_db.upsert_sweep_opportunity(
            sa_conn, org_id="orgA", tweet_id=t_vip, sweep_source="vip", score=85.0,
        )
    # @arf generates a reply from the feed: stamp replied (per-op) + the team-handle
    # call (which is VIP-exempt, so the opp stays active for the bank).
    with immediate_txn(sa_conn):
        relay_db.set_operator_opportunity_state(
            sa_conn, opportunity_id=o_vip, operator_handle="@arf", state="replied",
        )
        relay_db.mark_opportunity_handled(sa_conn, o_vip)

    assert _status(sa_conn, o_vip) == "active"  # banked, not team-depressed
    arf = {f["id"] for f in relay_db.list_feed_opportunities(sa_conn, "orgA", "@arf")}
    assert o_vip not in arf  # left @arf's feed (they replied)
    ben_feed = relay_db.list_feed_opportunities(sa_conn, "orgA", "@ben")
    ben = {f["id"] for f in ben_feed}
    assert o_vip in ben  # still banked for @ben
    # And it's still PROMINENT for @ben (active bucket, not depressed to handled).
    assert ben_feed[0]["id"] == o_vip


def test_non_vip_team_depressed_after_one_reply(sa_conn):
    """Contrast: a NON-VIP opportunity IS team-depressed after one operator replies
    (current behavior) — @arf loses it; @ben sees it but depressed to the bottom."""
    _seed(sa_conn)
    sa_conn.commit()
    t_hi = _seed_tweet(sa_conn, "nv-hi")
    t_nv = _seed_tweet(sa_conn, "nv-1")
    with immediate_txn(sa_conn):
        o_hi = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t_hi, sweep_source="topic", score=0.1)
        o_nv = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t_nv, sweep_source="topic", score=0.99)
    with immediate_txn(sa_conn):
        relay_db.set_operator_opportunity_state(
            sa_conn, opportunity_id=o_nv, operator_handle="@arf", state="replied",
        )
        relay_db.mark_opportunity_handled(sa_conn, o_nv)

    assert _status(sa_conn, o_nv) == "handled"  # team-depressed (non-VIP)
    arf = {f["id"] for f in relay_db.list_feed_opportunities(sa_conn, "orgA", "@arf")}
    assert o_nv not in arf  # @arf replied -> gone from their feed
    ben_feed = relay_db.list_feed_opportunities(sa_conn, "orgA", "@ben")
    # @ben still sees it (handled rows aren't excluded) but DEPRESSED to the bottom,
    # below the lower-scored active row — the current team-handled semantics.
    assert ben_feed[-1]["id"] == o_nv
    assert ben_feed[0]["id"] == o_hi
