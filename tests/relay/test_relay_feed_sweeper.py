"""C2.4 tests — sweeper: expiry, §15.5 retention GC, stuck-claims, reconciliation.

No real network: the reconciliation Sender seam is a deterministic fake. Old
timestamps are seeded directly to exercise each retention window; rows inside
their window are asserted retained.

Coverage (per MEGAPLAN C2.4 tests line):
  * sweeper expiry: pending submissions past expires_at → expired
  * stuck-claim reset >5min → retry
  * reconciliation finds an orphan external message → records + marks done
    (instead of re-sending); finds none → recycles to retry
  * retention GC: relay_processed_updates >7d, relay_publication_jobs >30d
    post-done/dead, relay_reply_notifications >90d, relay_tweets raw >30d,
    relay_messages past its window — all swept; rows within window retained
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.txn import immediate_txn
from sable_platform.relay.feed import sweeper


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(*, days=0, seconds=0):
    return _iso(datetime.now(timezone.utc) - timedelta(days=days, seconds=seconds))


def _seed_org(conn, org_id="orgs"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id})
    return org_id


class FakeSender:
    """Reconciliation Sender — find_recent_message returns a scripted id (or None)."""

    def __init__(self, recent_message_id=None):
        self._recent = recent_message_id
        self.searches = []

    def send(self, **kw):  # not used by the sweeper
        raise AssertionError("sweeper must not call send()")

    def find_recent_message(self, *, destination_platform, destination_chat_id, tweet):
        self.searches.append((destination_platform, destination_chat_id))
        return self._recent


# ==========================================================================
# 1. Submission expiry
# ==========================================================================
def test_expire_overdue_pending_submissions(sa_conn):
    org_id = _seed_org(sa_conn)
    tweet_row = relay_db.upsert_tweet(sa_conn, x_id="1", x_author_handle="a")
    member_row = sa_conn.execute(
        text("INSERT INTO relay_members (display_name) VALUES ('m') RETURNING id")
    ).fetchone()
    member_id = int(member_row[0])
    # One overdue pending, one in-window pending.
    sa_conn.execute(
        text(
            "INSERT INTO relay_submissions "
            "(org_id, tweet_id, submitter_id, source_chat_id, source_message_id, "
            " source_role, status, expires_at) "
            "VALUES (:o, :t, :m, 'c', 'mid', 'operator', 'pending', :exp)"
        ),
        {"o": org_id, "t": tweet_row, "m": member_id, "exp": _ago(seconds=10)},
    )
    sa_conn.execute(
        text(
            "INSERT INTO relay_submissions "
            "(org_id, tweet_id, submitter_id, source_chat_id, source_message_id, "
            " source_role, status, expires_at) "
            "VALUES (:o, :t2, :m, 'c', 'mid2', 'operator', 'pending', :exp)"
        ),
        {"o": org_id, "t2": relay_db.upsert_tweet(sa_conn, x_id="2", x_author_handle="a"),
         "m": member_id, "exp": _iso(datetime.now(timezone.utc) + timedelta(hours=1))},
    )
    sa_conn.commit()

    n = sweeper.expire_submissions(sa_conn)
    assert n == 1
    statuses = sa_conn.execute(
        text("SELECT status FROM relay_submissions ORDER BY id")
    ).fetchall()
    assert sorted(s[0] for s in statuses) == ["expired", "pending"]


# ==========================================================================
# 2. Retention GC — all five windows
# ==========================================================================
def test_retention_gc_all_five_windows(sa_conn):
    org_id = _seed_org(sa_conn)

    # --- relay_processed_updates: one >7d (GC'd), one <7d (kept) ---
    sa_conn.execute(
        text("INSERT INTO relay_processed_updates (platform, update_id, processed_at) VALUES ('telegram','old', :t)"),
        {"t": _ago(days=8)},
    )
    sa_conn.execute(
        text("INSERT INTO relay_processed_updates (platform, update_id, processed_at) VALUES ('telegram','new', :t)"),
        {"t": _ago(days=1)},
    )

    # --- relay_tweets: one with raw >30d (raw nulled, row kept), one <30d (kept) ---
    old_tweet = relay_db.upsert_tweet(sa_conn, x_id="100", x_author_handle="a", raw_json='{"big":"payload"}')
    new_tweet = relay_db.upsert_tweet(sa_conn, x_id="101", x_author_handle="a", raw_json='{"fresh":1}')
    sa_conn.execute(text("UPDATE relay_tweets SET fetched_at = :t WHERE id = :id"), {"t": _ago(days=31), "id": old_tweet})
    sa_conn.execute(text("UPDATE relay_tweets SET fetched_at = :t WHERE id = :id"), {"t": _ago(days=2), "id": new_tweet})

    # --- relay_publication_jobs: terminal >30d (GC'd), terminal <30d (kept), live >30d (kept) ---
    # Distinct destination chat ids so the partial dedupe index (over
    # pending/claimed/done) does not collide across these fixture rows.
    def _job(tweet_id, state, created, chat):
        sa_conn.execute(
            text(
                "INSERT INTO relay_publication_jobs "
                "(org_id, tweet_id, destination_platform, destination_chat_id, state, created_at) "
                "VALUES (:o, :t, 'discord', :ch, :s, :c)"
            ),
            {"o": org_id, "t": tweet_id, "ch": chat, "s": state, "c": created},
        )
    _job(old_tweet, "done", _ago(days=40), "cA")   # GC'd
    _job(old_tweet, "dead", _ago(days=5), "cB")     # kept (in window)
    _job(old_tweet, "pending", _ago(days=99), "cC") # kept (live state never GC'd)

    # --- relay_chats + relay_messages: one >90d (GC'd), one <90d (kept) ---
    chat_id = relay_db.upsert_chat(sa_conn, org_id, "chat1", platform="telegram")
    for emi, age in (("oldmsg", 91), ("newmsg", 10)):
        sa_conn.execute(
            text(
                "INSERT INTO relay_messages (org_id, chat_id, platform, external_message_id, received_at) "
                "VALUES (:o, :c, 'telegram', :e, :t)"
            ),
            {"o": org_id, "c": chat_id, "e": emi, "t": _ago(days=age)},
        )

    # --- relay_reply_notifications: one >90d (GC'd), one <90d (kept) ---
    member_row = sa_conn.execute(
        text("INSERT INTO relay_members (display_name) VALUES ('m') RETURNING id")
    ).fetchone()
    member_id = int(member_row[0])
    opp_row = sa_conn.execute(
        text(
            "INSERT INTO relay_reply_opportunities (org_id, tweet_id, flagger_id, origin) "
            "VALUES (:o, :t, :m, 'explicit_command') RETURNING id"
        ),
        {"o": org_id, "t": old_tweet, "m": member_id},
    ).fetchone()
    opp_id = int(opp_row[0])
    sa_conn.execute(
        text("INSERT INTO relay_reply_notifications (opportunity_id, member_id, notified_at) VALUES (:opp, :m, :t)"),
        {"opp": opp_id, "m": member_id, "t": _ago(days=91)},
    )
    # A 2nd member for the in-window notification (unique on opportunity_id, member_id).
    member2 = int(sa_conn.execute(text("INSERT INTO relay_members (display_name) VALUES ('m2') RETURNING id")).fetchone()[0])
    sa_conn.execute(
        text("INSERT INTO relay_reply_notifications (opportunity_id, member_id, notified_at) VALUES (:opp, :m, :t)"),
        {"opp": opp_id, "m": member2, "t": _ago(days=10)},
    )
    sa_conn.commit()

    result = sweeper.run_retention_gc(sa_conn)

    assert result.processed_updates == 1
    assert result.publication_jobs == 1
    assert result.reply_notifications == 1
    assert result.tweets_raw_cleared == 1
    assert result.messages == 1
    # chat1 still has an in-window message (newmsg) → NOT an orphan → not swept.
    assert result.chats == 0
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_chats")).scalar() == 1

    # Within-window rows retained.
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_processed_updates")).scalar() == 1
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_publication_jobs")).scalar() == 2  # dead-in-window + live
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_reply_notifications")).scalar() == 1
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_messages")).scalar() == 1
    # The old tweet's raw is nulled but the ROW survives; the fresh raw is intact.
    assert sa_conn.execute(text("SELECT raw FROM relay_tweets WHERE id = :id"), {"id": old_tweet}).scalar() is None
    assert sa_conn.execute(text("SELECT raw FROM relay_tweets WHERE id = :id"), {"id": new_tweet}).scalar() is not None
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_tweets")).scalar() == 2  # no rows deleted


# ==========================================================================
# 3. Stuck-claim reset (>5min)
# ==========================================================================
def _seed_claimed_job(conn, org_id, tweet_row, *, claimed_age_seconds):
    conn.execute(
        text(
            "INSERT INTO relay_publication_jobs "
            "(org_id, tweet_id, destination_platform, destination_chat_id, state, claimed_by, claimed_at, next_attempt_at) "
            "VALUES (:o, :t, 'discord', 'chan', 'claimed', 'w1', :ca, :na) "
        ),
        {"o": org_id, "t": tweet_row, "ca": _ago(seconds=claimed_age_seconds), "na": _ago(seconds=claimed_age_seconds)},
    )
    return conn.execute(text("SELECT id FROM relay_publication_jobs ORDER BY id DESC LIMIT 1")).scalar()


def test_stuck_claim_reset_after_5min(sa_conn):
    org_id = _seed_org(sa_conn)
    tweet_row = relay_db.upsert_tweet(sa_conn, x_id="1", x_author_handle="a")
    stuck_id = _seed_claimed_job(sa_conn, org_id, tweet_row, claimed_age_seconds=400)  # >5min
    sa_conn.commit()

    n = sweeper.reset_stuck_claims(sa_conn)
    assert n == 1
    state, attempts = sa_conn.execute(
        text("SELECT state, attempts FROM relay_publication_jobs WHERE id = :id"), {"id": stuck_id}
    ).fetchone()
    assert state == "retry"
    assert attempts == 1


def test_fresh_claim_not_reset(sa_conn):
    org_id = _seed_org(sa_conn)
    tweet_row = relay_db.upsert_tweet(sa_conn, x_id="1", x_author_handle="a")
    fresh_id = _seed_claimed_job(sa_conn, org_id, tweet_row, claimed_age_seconds=30)  # <5min
    sa_conn.commit()
    n = sweeper.reset_stuck_claims(sa_conn)
    assert n == 0
    assert sa_conn.execute(
        text("SELECT state FROM relay_publication_jobs WHERE id = :id"), {"id": fresh_id}
    ).scalar() == "claimed"


# ==========================================================================
# 4. Reconciliation (§3.2) — orphan external message found vs not
# ==========================================================================
def test_reconcile_finds_orphan_message_records_and_marks_done(sa_conn):
    org_id = _seed_org(sa_conn)
    tweet_row = relay_db.upsert_tweet(sa_conn, x_id="900", x_author_handle="a")
    job_id = _seed_claimed_job(sa_conn, org_id, tweet_row, claimed_age_seconds=120)  # >60s
    sa_conn.commit()

    sender = FakeSender(recent_message_id="found-msg-77")
    result = sweeper.reconcile_orphan_claims(sa_conn, sender)

    assert result.reconciled_done == 1
    assert result.recycled_retry == 0
    # Job marked done (NOT re-sent — send() would have raised).
    assert sa_conn.execute(
        text("SELECT state FROM relay_publication_jobs WHERE id = :id"), {"id": job_id}
    ).scalar() == "done"
    # The orphan publication is now recorded with the found message id.
    pub = sa_conn.execute(
        text("SELECT destination_message_id FROM relay_publications WHERE tweet_id = :t"), {"t": tweet_row}
    ).fetchone()
    assert pub[0] == "found-msg-77"


def test_reconcile_no_orphan_recycles_to_retry(sa_conn):
    org_id = _seed_org(sa_conn)
    tweet_row = relay_db.upsert_tweet(sa_conn, x_id="901", x_author_handle="a")
    job_id = _seed_claimed_job(sa_conn, org_id, tweet_row, claimed_age_seconds=120)
    sa_conn.commit()

    sender = FakeSender(recent_message_id=None)  # no orphan found
    result = sweeper.reconcile_orphan_claims(sa_conn, sender)

    assert result.reconciled_done == 0
    assert result.recycled_retry == 1
    assert sa_conn.execute(
        text("SELECT state FROM relay_publication_jobs WHERE id = :id"), {"id": job_id}
    ).scalar() == "retry"
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_publications")).scalar() == 0


def test_reconcile_ignores_fresh_claims(sa_conn):
    org_id = _seed_org(sa_conn)
    tweet_row = relay_db.upsert_tweet(sa_conn, x_id="902", x_author_handle="a")
    _seed_claimed_job(sa_conn, org_id, tweet_row, claimed_age_seconds=10)  # <60s
    sa_conn.commit()
    sender = FakeSender(recent_message_id="x")
    result = sweeper.reconcile_orphan_claims(sa_conn, sender)
    assert result.claims_examined == 0
    assert sender.searches == []


# ==========================================================================
# Full sweep orchestration
# ==========================================================================
def test_run_sweep_runs_all_duties(sa_conn):
    org_id = _seed_org(sa_conn)
    tweet_row = relay_db.upsert_tweet(sa_conn, x_id="1", x_author_handle="a")
    _seed_claimed_job(sa_conn, org_id, tweet_row, claimed_age_seconds=120)
    sa_conn.execute(
        text("INSERT INTO relay_processed_updates (platform, update_id, processed_at) VALUES ('telegram','old', :t)"),
        {"t": _ago(days=8)},
    )
    sa_conn.commit()

    sender = FakeSender(recent_message_id="m")
    result = sweeper.run_sweep(sa_conn, sender)
    assert result.reconcile.reconciled_done == 1
    assert result.retention.processed_updates == 1


# ==========================================================================
# 5. relay_chats orphan GC (§15.5 line 865) — the 6th retention window
# ==========================================================================
def test_relay_chats_orphan_gc_sweeps_only_unreferenced_chats(sa_conn):
    """A relay_chats row is swept ONLY once no binding references it AND no
    in-window relay_message points at it; referenced/in-window chats are kept."""
    org_id = _seed_org(sa_conn)

    # Chat A: ORPHAN — no binding, only an OUT-of-window (>90d) message → swept.
    chat_a = relay_db.upsert_chat(sa_conn, org_id, "chatA", platform="telegram")
    sa_conn.execute(
        text(
            "INSERT INTO relay_messages (org_id, chat_id, platform, external_message_id, received_at) "
            "VALUES (:o, :c, 'telegram', 'a-old', :t)"
        ),
        {"o": org_id, "c": chat_a, "t": _ago(days=91)},
    )

    # Chat B: kept — still referenced by an ACTIVE binding (no messages at all).
    chat_b = relay_db.upsert_chat(sa_conn, org_id, "chatB", platform="telegram")
    sa_conn.execute(
        text(
            "INSERT INTO relay_chat_bindings (org_id, platform, chat_id, role, status) "
            "VALUES (:o, 'telegram', 'chatB', 'community', 'active')"
        ),
        {"o": org_id},
    )

    # Chat C: kept — no binding, but an IN-window (<90d) message points at it.
    chat_c = relay_db.upsert_chat(sa_conn, org_id, "chatC", platform="telegram")
    sa_conn.execute(
        text(
            "INSERT INTO relay_messages (org_id, chat_id, platform, external_message_id, received_at) "
            "VALUES (:o, :c, 'telegram', 'c-new', :t)"
        ),
        {"o": org_id, "c": chat_c, "t": _ago(days=10)},
    )
    sa_conn.commit()

    # run_retention_gc sweeps relay_messages FIRST (drops chatA's out-of-window
    # message) then relay_chats — so chatA is freshly orphaned and eligible.
    result = sweeper.run_retention_gc(sa_conn)
    assert result.messages == 1  # chatA's old message swept
    assert result.chats == 1  # only chatA reclaimed

    surviving = {
        r[0]
        for r in sa_conn.execute(text("SELECT id FROM relay_chats ORDER BY id")).fetchall()
    }
    assert surviving == {chat_b, chat_c}
    assert chat_a not in surviving


def test_relay_chats_gc_helper_direct(sa_conn):
    """gc_orphan_chats in isolation: an unbound, message-less chat is an orphan."""
    org_id = _seed_org(sa_conn)
    orphan = relay_db.upsert_chat(sa_conn, org_id, "lonely", platform="discord")
    sa_conn.commit()
    with immediate_txn(sa_conn):
        n = relay_db.gc_orphan_chats(sa_conn)
    assert n == 1
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_chats WHERE id=:id"), {"id": orphan}).scalar() == 0


# ==========================================================================
# 6. §15.6 "after N attempts → dead" on the SWEEPER recycle paths
# ==========================================================================
def test_stuck_claim_at_max_attempts_is_killed_dead(sa_conn):
    """A stuck claim whose attempts are exhausted is killed 'dead', not recycled
    (§15.6 — otherwise a crash-looping worker loops claimed→retry forever)."""
    from sable_platform.relay.feed.publisher import MAX_ATTEMPTS

    org_id = _seed_org(sa_conn)
    tweet_row = relay_db.upsert_tweet(sa_conn, x_id="1", x_author_handle="a")
    stuck_id = _seed_claimed_job(sa_conn, org_id, tweet_row, claimed_age_seconds=400)
    # One short of the cap so attempts+1 >= MAX_ATTEMPTS on this recycle.
    sa_conn.execute(
        text("UPDATE relay_publication_jobs SET attempts=:a WHERE id=:id"),
        {"a": MAX_ATTEMPTS - 1, "id": stuck_id},
    )
    sa_conn.commit()

    sweeper.reset_stuck_claims(sa_conn)
    state, attempts = sa_conn.execute(
        text("SELECT state, attempts FROM relay_publication_jobs WHERE id=:id"), {"id": stuck_id}
    ).fetchone()
    assert state == "dead"
    assert attempts == MAX_ATTEMPTS  # the final exhausting attempt counted


def test_stuck_claim_below_max_attempts_still_recycles_to_retry(sa_conn):
    org_id = _seed_org(sa_conn)
    tweet_row = relay_db.upsert_tweet(sa_conn, x_id="1", x_author_handle="a")
    stuck_id = _seed_claimed_job(sa_conn, org_id, tweet_row, claimed_age_seconds=400)
    # attempts=0 → far below cap → recycle, not kill.
    sa_conn.commit()
    sweeper.reset_stuck_claims(sa_conn)
    assert sa_conn.execute(
        text("SELECT state FROM relay_publication_jobs WHERE id=:id"), {"id": stuck_id}
    ).scalar() == "retry"


def test_reconcile_no_orphan_at_max_attempts_kills_dead(sa_conn):
    """The reconcile no-orphan branch ALSO kills 'dead' at attempts exhaustion."""
    from sable_platform.relay.feed.publisher import MAX_ATTEMPTS

    org_id = _seed_org(sa_conn)
    tweet_row = relay_db.upsert_tweet(sa_conn, x_id="903", x_author_handle="a")
    job_id = _seed_claimed_job(sa_conn, org_id, tweet_row, claimed_age_seconds=120)
    sa_conn.execute(
        text("UPDATE relay_publication_jobs SET attempts=:a WHERE id=:id"),
        {"a": MAX_ATTEMPTS - 1, "id": job_id},
    )
    sa_conn.commit()

    sender = FakeSender(recent_message_id=None)  # orphan MISS
    result = sweeper.reconcile_orphan_claims(sa_conn, sender)
    assert result.killed_dead == 1
    assert result.recycled_retry == 0
    assert sa_conn.execute(
        text("SELECT state FROM relay_publication_jobs WHERE id=:id"), {"id": job_id}
    ).scalar() == "dead"
    # No publication was recorded (the orphan was genuinely missing).
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_publications")).scalar() == 0
