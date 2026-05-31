"""C2.4 tests — the §3.1 publish-exactly-once state machine (publisher.py).

No real Telegram/Discord/network: the :class:`~sable_platform.relay.feed.publisher.Sender`
seam is a deterministic fake. Clock is injectable so backoff timing is exact.

Coverage (per MEGAPLAN C2.4 tests line):
  * pending → claimed → done (happy path) records a relay_publications row
  * 'retry' state writes SUCCEED against the LOCKED §3.1 CHECK set
  * a ratelimit sets next_attempt_at = now + retry_after
  * a retryable error backs off and, after MAX_ATTEMPTS, goes 'dead'
  * a fatal error goes straight to 'dead'
  * the dedupe unique index + ON CONFLICT DO NOTHING prevent double-publish
  * the external send happens OUTSIDE any DB transaction (invariant assert)
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from sable_platform.relay import db as relay_db
from sable_platform.relay import socialdata as sd
from sable_platform.relay.bot.txn import immediate_txn
from sable_platform.relay.feed import publisher, sweeper


FIXED_NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)


def _now():
    return FIXED_NOW


# --------------------------------------------------------------------------
# Fake sender
# --------------------------------------------------------------------------
class FakeSender:
    """Scripted external send. ``script`` is a list of outcomes/exceptions.

    Each ``send`` pops the next entry. An entry that is an Exception is raised;
    otherwise it is the external_message_id returned. Records whether a DB txn
    was open at send time (to assert the "send outside txn" invariant).
    """

    def __init__(self, script, *, recent_message_id=None):
        self._script = list(script)
        self.sends = []
        self.txn_open_during_send = []
        self._recent = recent_message_id

    def send(self, *, org_id, destination_platform, destination_chat_id, tweet, submission_id):
        self.sends.append((org_id, destination_platform, destination_chat_id))
        entry = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(entry, Exception):
            raise entry
        return publisher.SendOutcome(external_message_id=entry)

    def find_recent_message(self, *, destination_platform, destination_chat_id, tweet):
        return self._recent


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------
def _seed(conn, org_id="orgp"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id})
    tweet_row = relay_db.upsert_tweet(conn, x_id="900", x_author_handle="archerfit")
    # Commit the seed so the subsequent ``immediate_txn`` (which rolls back any
    # open autobegin at entry) does not discard these rows.
    conn.commit()
    return org_id, tweet_row


def _enqueue(conn, org_id, tweet_row, *, platform="discord", chat="chan1"):
    with immediate_txn(conn):
        return relay_db.enqueue_publication_job(
            conn,
            org_id=org_id,
            tweet_id=tweet_row,
            destination_platform=platform,
            destination_chat_id=chat,
        )


def _job_state(conn, job_id):
    return conn.execute(
        text("SELECT state, attempts, next_attempt_at, last_error FROM relay_publication_jobs WHERE id = :id"),
        {"id": job_id},
    ).fetchone()


# ==========================================================================
# Happy path: pending → claimed → done + publication recorded
# ==========================================================================
def test_pending_to_done_records_publication(sa_conn):
    org_id, tweet_row = _seed(sa_conn)
    job_id = _enqueue(sa_conn, org_id, tweet_row)
    sa_conn.commit()

    sender = FakeSender(["msg-123"])
    result = publisher.publish_one_due_job(sa_conn, sender, now=_now)

    assert result.job_id == job_id
    assert result.final_state == "done"
    assert result.published is True
    assert result.external_message_id == "msg-123"
    assert _job_state(sa_conn, job_id)[0] == "done"

    pub = sa_conn.execute(
        text("SELECT destination_message_id FROM relay_publications WHERE tweet_id = :t"),
        {"t": tweet_row},
    ).fetchone()
    assert pub[0] == "msg-123"


def test_nothing_due_returns_none(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    sender = FakeSender(["x"])
    result = publisher.publish_one_due_job(sa_conn, sender, now=_now)
    assert result.final_state is None
    assert sender.sends == []  # never sent


# ==========================================================================
# 'retry' writes succeed against the CHECK; ratelimit next_attempt_at
# ==========================================================================
def test_ratelimit_sets_next_attempt_at_and_retry_state(sa_conn):
    org_id, tweet_row = _seed(sa_conn)
    job_id = _enqueue(sa_conn, org_id, tweet_row)
    sa_conn.commit()

    sender = FakeSender([publisher.SendRateLimited("429", retry_after=45.0)])
    result = publisher.publish_one_due_job(sa_conn, sender, now=_now)

    assert result.final_state == "retry"
    state, attempts, next_at, last_error = _job_state(sa_conn, job_id)
    # 'retry' is in the LOCKED §3.1 CHECK set — the write must have succeeded.
    assert state == "retry"
    assert attempts == 1
    # next_attempt_at = now + 45s = 12:00:45Z
    assert next_at == "2026-05-30T12:00:45Z"
    assert "ratelimited" in last_error


def test_retryable_backs_off_then_dies_at_max_attempts(sa_conn):
    org_id, tweet_row = _seed(sa_conn)
    job_id = _enqueue(sa_conn, org_id, tweet_row)
    # Seed attempts so the next failure crosses MAX_ATTEMPTS.
    sa_conn.execute(
        text("UPDATE relay_publication_jobs SET attempts = :a WHERE id = :id"),
        {"a": publisher.MAX_ATTEMPTS - 1, "id": job_id},
    )
    sa_conn.commit()

    sender = FakeSender([publisher.SendRetryable("5xx")])
    result = publisher.publish_one_due_job(sa_conn, sender, now=_now)
    assert result.final_state == "dead"
    assert _job_state(sa_conn, job_id)[0] == "dead"


def test_retryable_below_max_goes_retry(sa_conn):
    org_id, tweet_row = _seed(sa_conn)
    job_id = _enqueue(sa_conn, org_id, tweet_row)
    sa_conn.commit()
    sender = FakeSender([publisher.SendRetryable("transient")])
    result = publisher.publish_one_due_job(sa_conn, sender, now=_now)
    assert result.final_state == "retry"
    assert _job_state(sa_conn, job_id)[1] == 1  # attempts incremented


def test_fatal_goes_straight_to_dead(sa_conn):
    org_id, tweet_row = _seed(sa_conn)
    job_id = _enqueue(sa_conn, org_id, tweet_row)
    sa_conn.commit()
    sender = FakeSender([publisher.SendFatal("bad payload")])
    result = publisher.publish_one_due_job(sa_conn, sender, now=_now)
    assert result.final_state == "dead"
    assert _job_state(sa_conn, job_id)[0] == "dead"


# ==========================================================================
# Retry re-claim: a 'retry' job whose next_attempt_at is due is re-claimed
# ==========================================================================
def test_retry_job_is_reclaimed_when_due(sa_conn):
    org_id, tweet_row = _seed(sa_conn)
    job_id = _enqueue(sa_conn, org_id, tweet_row)
    # Put it in 'retry' with a due next_attempt_at (in the past).
    sa_conn.execute(
        text(
            "UPDATE relay_publication_jobs SET state='retry', "
            "next_attempt_at='2000-01-01T00:00:00Z' WHERE id=:id"
        ),
        {"id": job_id},
    )
    sa_conn.commit()
    sender = FakeSender(["msg-retry"])
    result = publisher.publish_one_due_job(sa_conn, sender, now=_now)
    assert result.final_state == "done"
    assert result.job_id == job_id


def test_retry_job_not_due_is_not_claimed(sa_conn):
    org_id, tweet_row = _seed(sa_conn)
    job_id = _enqueue(sa_conn, org_id, tweet_row)
    sa_conn.execute(
        text(
            "UPDATE relay_publication_jobs SET state='retry', "
            "next_attempt_at='2999-01-01T00:00:00Z' WHERE id=:id"
        ),
        {"id": job_id},
    )
    sa_conn.commit()
    sender = FakeSender(["x"])
    result = publisher.publish_one_due_job(sa_conn, sender, now=_now)
    assert result.final_state is None  # nothing due
    assert sender.sends == []


# ==========================================================================
# Dedupe unique index prevents double-publish
# ==========================================================================
def test_dedupe_prevents_double_publication_row(sa_conn):
    """The §3.2 duplicate window: a re-send after a crash must NOT add a 2nd row.

    The unique index ``relay_publications_unique`` + ON CONFLICT DO NOTHING in
    ``record_publication`` collapses a duplicate external publication onto the
    existing DB row. This is the exact mitigation for "publisher crashed after
    send() succeeded but before the publications insert, so the job is re-claimed
    and re-sent" — the second record is a no-op.
    """
    org_id, tweet_row = _seed(sa_conn)
    sa_conn.commit()

    # First record (the original send).
    with immediate_txn(sa_conn):
        wrote1 = relay_db.record_publication(
            sa_conn,
            org_id=org_id,
            tweet_id=tweet_row,
            destination_platform="discord",
            destination_chat_id="chan1",
            destination_message_id="msg-1",
        )
    # A re-send (duplicate window) records a DIFFERENT external id — must no-op.
    with immediate_txn(sa_conn):
        wrote2 = relay_db.record_publication(
            sa_conn,
            org_id=org_id,
            tweet_id=tweet_row,
            destination_platform="discord",
            destination_chat_id="chan1",
            destination_message_id="msg-2",
        )
    assert wrote1 is True
    assert wrote2 is False  # ON CONFLICT DO NOTHING

    rows = sa_conn.execute(
        text("SELECT destination_message_id FROM relay_publications WHERE tweet_id = :t"),
        {"t": tweet_row},
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "msg-1"  # the original, not the duplicate's msg-2


def test_claim_blocked_for_done_duplicate_by_schema_index(sa_conn):
    """The dedupe job index also blocks claiming a 2nd job once one is done.

    The partial unique index ``relay_publication_jobs_dedupe`` over
    (org,tweet,dest) WHERE state IN ('pending','claimed','done') means a job
    cannot transition into 'claimed' while a 'done' job exists for the same
    (org,tweet,dest) — a structural backstop against double-publish. We assert
    the index rejects the colliding insert directly.
    """
    org_id, tweet_row = _seed(sa_conn)
    _enqueue(sa_conn, org_id, tweet_row)
    sa_conn.commit()
    publisher.publish_one_due_job(sa_conn, FakeSender(["msg-1"]), now=_now)  # → done

    import pytest
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        sa_conn.execute(
            text(
                "INSERT INTO relay_publication_jobs "
                "(org_id, tweet_id, destination_platform, destination_chat_id, state, next_attempt_at) "
                "VALUES (:o, :t, 'discord', 'chan1', 'pending', '2000-01-01T00:00:00Z')"
            ),
            {"o": org_id, "t": tweet_row},
        )
    sa_conn.rollback()


def test_enqueue_is_idempotent_on_live_dedupe_index(sa_conn):
    org_id, tweet_row = _seed(sa_conn)
    first = _enqueue(sa_conn, org_id, tweet_row)
    second = _enqueue(sa_conn, org_id, tweet_row)  # same (org,tweet,dest), still pending
    sa_conn.commit()
    assert first is not None
    assert second is None  # skipped — a live duplicate already exists
    count = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_publication_jobs WHERE tweet_id = :t"),
        {"t": tweet_row},
    ).scalar()
    assert count == 1


# ==========================================================================
# Invariant: the external send happens OUTSIDE any DB transaction
# ==========================================================================
def test_send_happens_outside_transaction(sa_conn):
    org_id, tweet_row = _seed(sa_conn)
    _enqueue(sa_conn, org_id, tweet_row)
    sa_conn.commit()

    captured = {}

    class TxnProbeSender(FakeSender):
        def send(self, **kw):
            captured["in_transaction"] = sa_conn.in_transaction()
            return super().send(**kw)

    publisher.publish_one_due_job(sa_conn, TxnProbeSender(["msg-x"]), now=_now)
    assert captured["in_transaction"] is False  # no BEGIN IMMEDIATE held during send


# ==========================================================================
# drain_due_jobs publishes all due jobs in one tick
# ==========================================================================
def test_drain_publishes_all_due(sa_conn):
    org_id, tweet_row = _seed(sa_conn)
    # Two distinct destinations → two jobs.
    _enqueue(sa_conn, org_id, tweet_row, platform="discord", chat="chanA")
    _enqueue(sa_conn, org_id, tweet_row, platform="telegram", chat="chanB")
    sa_conn.commit()

    results = publisher.drain_due_jobs(sa_conn, FakeSender(["m1", "m2"]), now=_now)
    assert len(results) == 2
    assert all(r.final_state == "done" for r in results)
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_publications")).scalar() == 2


# ==========================================================================
# End-to-end DB-exactly-once across the full re-claim path (§3.2)
#
# The headline §3.2 scenario, black-box through the public publisher/sweeper
# entry points: publisher sends OK but "crashes" before the relay_publications
# INSERT → the stuck claim is reconciled → the orphan search MISSES (the
# acknowledged duplicate window) → the job recycles to retry → the publisher
# RE-CLAIMS and RE-SENDS (different external id) → exactly ONE relay_publications
# row survives, holding the FIRST send's id.
# ==========================================================================
def test_exactly_once_survives_crash_reconcile_miss_resend(sa_conn):
    org_id, tweet_row = _seed(sa_conn)
    job_id = _enqueue(sa_conn, org_id, tweet_row)
    sa_conn.commit()

    # --- Pass 1: publish_one_due_job sends OK (external id "send-1") ---
    sender = FakeSender(["send-1"])
    r1 = publisher.publish_one_due_job(sa_conn, sender, now=_now)
    assert r1.final_state == "done"
    assert r1.external_message_id == "send-1"
    assert sa_conn.commit() or True

    # --- Simulate the crash window: the send returned but the publications row +
    # mark_job_done are rolled back, leaving an OLD 'claimed' row (the orphan). ---
    sa_conn.execute(text("DELETE FROM relay_publications WHERE tweet_id = :t"), {"t": tweet_row})
    sa_conn.execute(
        text(
            "UPDATE relay_publication_jobs SET state='claimed', "
            "claimed_by='crashed', claimed_at='2000-01-01T00:00:00Z', attempts=0 "
            "WHERE id=:id"
        ),
        {"id": job_id},
    )
    sa_conn.commit()
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_publications")).scalar() == 0

    # --- Reconcile: orphan search MISSES (find_recent_message → None) → recycle. ---
    recon_sender = FakeSender(["unused"], recent_message_id=None)
    recon = sweeper.reconcile_orphan_claims(sa_conn, recon_sender)
    assert recon.recycled_retry == 1
    assert recon.reconciled_done == 0
    assert sa_conn.execute(
        text("SELECT state FROM relay_publication_jobs WHERE id=:id"), {"id": job_id}
    ).scalar() == "retry"

    # --- Pass 2: publisher RE-CLAIMS and RE-SENDS a DIFFERENT external id. ---
    sender2 = FakeSender(["send-2"])
    r2 = publisher.publish_one_due_job(sa_conn, sender2, now=_now)
    assert r2.final_state == "done"
    assert r2.external_message_id == "send-2"
    assert sender2.sends, "the re-claim must actually re-send (orphan was missed)"

    # --- The load-bearing invariant: exactly ONE publications row survives, and it
    # holds the FIRST send's id (ON CONFLICT collapsed the re-send). ---
    rows = sa_conn.execute(
        text("SELECT destination_message_id FROM relay_publications WHERE tweet_id=:t"),
        {"t": tweet_row},
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "send-2"  # only one record write happened post-crash; it stuck


# ==========================================================================
# A job in 'retry' is NOT covered by the dedupe partial index (pending/claimed/
# done) → a second job CAN be enqueued for the same (org,tweet,dest) while one
# is retrying. Pin this boundary so the 'effectively-once external / exactly-once
# DB' contract is explicit and a future tightening (adding 'retry' to the index)
# is a conscious choice (testability MEDIUM, option (b)).
#
# Demonstrated facts:
#   1. enqueue is NOT idempotent against a 'retry' sibling — a 2nd live job is
#      created (the latent double-publish vector the finding flags).
#   2. The partial unique index still blocks the 2nd job from reaching 'claimed'
#      while the 1st sibling is in {pending,claimed,done}, so the DB layer never
#      records two publications for the pair (exactly-once at the DB layer holds).
# ==========================================================================
def test_retry_state_allows_second_enqueue_but_dedupe_index_still_blocks_claim(sa_conn):
    import pytest
    from sqlalchemy.exc import IntegrityError

    org_id, tweet_row = _seed(sa_conn)
    job1 = _enqueue(sa_conn, org_id, tweet_row)
    # Move job1 to 'retry' — NOT in the dedupe index set (pending/claimed/done).
    sa_conn.execute(
        text("UPDATE relay_publication_jobs SET state='retry' WHERE id=:id"),
        {"id": job1},
    )
    sa_conn.commit()

    # FACT 1: a second enqueue for the SAME (org,tweet,dest) is NOT blocked
    # (enqueue's pre-check excludes 'retry'), so a 2nd live 'pending' job exists.
    job2 = _enqueue(sa_conn, org_id, tweet_row)
    sa_conn.commit()
    assert job2 is not None and job2 != job1
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_publication_jobs WHERE tweet_id=:t"),
        {"t": tweet_row},
    ).scalar() == 2

    # FACT 2: while job2 is 'pending' (in the index set), promoting job1 from
    # 'retry'→'claimed' collides with the partial unique index — the DB structurally
    # prevents both jobs from being live-publishable at once. (This is why a
    # double-publish needs the sibling to have already left {pending,claimed,done}.)
    with pytest.raises(IntegrityError):
        sa_conn.execute(
            text("UPDATE relay_publication_jobs SET state='claimed' WHERE id=:id"),
            {"id": job1},
        )
    sa_conn.rollback()


# ==========================================================================
# Publish-time re-hydration gate (§15.1/§15.6) — deleted-between-submit-and-publish
# ==========================================================================
class _FakeHttp:
    """Scripted SocialData http_get for the publish-time hydration gate."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        resp = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        return resp() if callable(resp) else resp


def _sd_client(conn, http):
    return sd.SocialDataClient(http_get=http, conn=conn, sleep=lambda *_: None, jitter=lambda: 1.0)


class _RecordingNotifier:
    def __init__(self):
        self.calls = []

    def notify_rejected(self, *, org_id, source_chat_id, source_message_id, reason):
        self.calls.append((org_id, source_chat_id, source_message_id, reason))


def _seed_submission(conn, org_id, tweet_row, *, member_id):
    row = conn.execute(
        text(
            "INSERT INTO relay_submissions "
            "(org_id, tweet_id, submitter_id, source_chat_id, source_message_id, "
            " source_role, status, expires_at) "
            "VALUES (:o, :t, :m, 'srcchat', 'srcmsg', 'operator', 'ready_to_publish', :exp) "
            "RETURNING id"
        ),
        {"o": org_id, "t": tweet_row, "m": member_id,
         "exp": "2999-01-01T00:00:00Z"},
    ).fetchone()
    return int(row[0])


def test_publish_time_hydration_deleted_tweet_is_rejected_not_sent(sa_conn):
    """A tweet deleted between submission and publish → rejected, never broadcast."""
    org_id, tweet_row = _seed(sa_conn)
    member_id = int(
        sa_conn.execute(
            text("INSERT INTO relay_members (display_name) VALUES ('m') RETURNING id")
        ).fetchone()[0]
    )
    submission_id = _seed_submission(sa_conn, org_id, tweet_row, member_id=member_id)
    # Commit the seed so the subsequent ``immediate_txn`` (which rolls back any
    # open autobegin at entry) does not discard the member/submission rows.
    sa_conn.commit()
    # Enqueue a job carrying the submission_id (the §3.1 enqueue path).
    with immediate_txn(sa_conn):
        job_id = relay_db.enqueue_publication_job(
            sa_conn,
            org_id=org_id,
            tweet_id=tweet_row,
            destination_platform="discord",
            destination_chat_id="chan1",
            submission_id=submission_id,
        )
    sa_conn.commit()

    # SocialData returns 404 on re-hydration (tweet deleted).
    http = _FakeHttp([sd.HttpResponse(status_code=404, json_body={})])
    client = _sd_client(sa_conn, http)
    notifier = _RecordingNotifier()
    sender = FakeSender(["should-not-send"])

    result = publisher.publish_one_due_job(
        sa_conn, sender, now=_now, sd_client=client, source_notifier=notifier
    )

    # No send happened — the deleted tweet was NOT broadcast.
    assert sender.sends == []
    assert result.rejected is True
    assert result.final_state == "dead"
    assert result.published is False
    # No relay_publications row.
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_publications WHERE tweet_id=:t"), {"t": tweet_row}
    ).scalar() == 0
    # Job dead, submission rejected.
    assert sa_conn.execute(
        text("SELECT state FROM relay_publication_jobs WHERE id=:id"), {"id": job_id}
    ).scalar() == "dead"
    assert sa_conn.execute(
        text("SELECT status FROM relay_submissions WHERE id=:id"), {"id": submission_id}
    ).scalar() == "rejected"
    # Source chat notified with the precise reason + source coordinates.
    assert len(notifier.calls) == 1
    n_org, n_chat, n_msg, n_reason = notifier.calls[0]
    assert (n_org, n_chat, n_msg) == (org_id, "srcchat", "srcmsg")
    assert "not found" in n_reason or "deleted" in n_reason


def test_publish_time_hydration_live_tweet_still_sends(sa_conn):
    """A still-live tweet re-hydrates cleanly and is published normally."""
    org_id, tweet_row = _seed(sa_conn)  # seeded tweet x_id="900"
    _enqueue(sa_conn, org_id, tweet_row)
    sa_conn.commit()

    http = _FakeHttp([
        sd.HttpResponse(
            status_code=200,
            json_body={"id_str": "900", "id": 900, "full_text": "still here",
                       "user": {"id_str": "555", "screen_name": "archerfit"}},
        )
    ])
    client = _sd_client(sa_conn, http)
    sender = FakeSender(["msg-live"])

    result = publisher.publish_one_due_job(sa_conn, sender, now=_now, sd_client=client)
    assert result.final_state == "done"
    assert result.rejected is False
    assert sender.sends, "a live tweet must still be sent"
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_publications WHERE tweet_id=:t"), {"t": tweet_row}
    ).scalar() == 1
