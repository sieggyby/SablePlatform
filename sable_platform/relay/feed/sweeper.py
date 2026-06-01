"""Background sweeper — expiry, retention GC, stuck-claims, reconciliation (C2.4).

Runs on a slow cadence (PLAN §3.2 every ~60s) alongside the publisher. Five
duties:

  1. **Submission expiry** (:func:`expire_submissions`). ``pending`` submissions
     whose ``expires_at`` (the quorum window) has passed are marked ``expired``.

  2. **Retention GC — all SIX §15.5 windows, pinned here in one place**
     (:func:`run_retention_gc`):
       * ``relay_processed_updates``      — 7d post-``processed_at``
       * ``relay_publication_jobs``       — 30d post-``done``/``dead``
       * ``relay_reply_notifications``    — 90d
       * ``relay_tweets`` raw payload     — 30d (nulls ``raw``, keeps the row)
       * ``relay_messages``               — 90d bounded window
       * ``relay_chats``                  — binding-scoped (§15.5 line 865):
         swept only once no binding references the chat AND no in-window
         ``relay_messages`` row points at it; runs AFTER ``relay_messages``.
     Rows inside their window are retained.

  3. **Stuck-claim reset** (:func:`reset_stuck_claims`). A ``claimed`` job whose
     ``claimed_at`` is older than 5min (§3.1) is treated as an orphaned claim
     (worker crashed) and recycled to ``retry`` so it gets re-claimed.

  4. **Reconciliation** (:func:`reconcile_orphan_claims`, §3.2 — external
     effectively-once). Before recycling a stuck claim, best-effort SEARCH for a
     recent external message matching the job's ``tweet_id`` (the publisher's
     :meth:`Sender.find_recent_message`, called OUTSIDE any txn). If found, the
     ``send()`` had actually succeeded before the crash — record the publication
     (ON CONFLICT DO NOTHING) and mark the job ``done`` instead of re-sending
     (closing the §3.1 duplicate window). If not found, recycle to ``retry``.

All DB writes happen inside ``immediate_txn``; the only external call is the
reconciliation message-search, which happens BETWEEN transactions (never inside
a ``BEGIN IMMEDIATE``). The clock is injectable via the db-helper windows; tests
seed old timestamps to exercise each window.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.engine import Connection

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.txn import immediate_txn
from sable_platform.relay.feed.publisher import MAX_ATTEMPTS, Sender

logger = logging.getLogger(__name__)

# §3.1 stuck-claim threshold (a claim older than this is an orphaned claim).
STUCK_CLAIM_SECONDS = 5 * 60
# §3.2 reconciliation threshold (a claim older than this is checked for an orphan
# external message before recycling). Tighter than the stuck-claim window so the
# reconciliation pass gets first crack at the duplicate window.
RECONCILE_CLAIM_SECONDS = 60


# ---------------------------------------------------------------------------
# 1. Submission expiry
# ---------------------------------------------------------------------------
def expire_submissions(conn: Connection) -> int:
    """Expire ``pending`` submissions past their ``expires_at``. Returns the count."""
    with immediate_txn(conn):
        n = relay_db.expire_overdue_submissions(conn)
    if n:
        logger.info("relay sweeper: expired %s overdue pending submission(s)", n)
    return n


# ---------------------------------------------------------------------------
# 2. Retention GC — all five §15.5 windows
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RetentionGcResult:
    """Per-window deletion/clear counts (for logging + tests)."""

    processed_updates: int
    publication_jobs: int
    reply_notifications: int
    tweets_raw_cleared: int
    messages: int
    chats: int


def run_retention_gc(
    conn: Connection,
    *,
    processed_updates_days: int = 7,
    publication_jobs_days: int = 30,
    reply_notifications_days: int = 90,
    tweets_raw_days: int = 30,
    messages_days: int = 90,
) -> RetentionGcResult:
    """Run all six §15.5 retention GC windows in one pass.

    Each window's default matches §15.5 exactly:
      * ``relay_processed_updates``   7d
      * ``relay_publication_jobs``    30d (only terminal done/dead)
      * ``relay_reply_notifications`` 90d
      * ``relay_tweets`` raw          30d (nulls ``raw``, never deletes the row —
        deleting would orphan submissions/publications that FK to the tweet)
      * ``relay_messages``            90d bounded window
      * ``relay_chats``               binding-scoped (§15.5 line 865) — swept ONLY
        when no binding references the chat and no in-window ``relay_messages``
        row points at it. Runs AFTER ``relay_messages`` so a chat whose last
        in-window message was just swept becomes eligible this same pass.
    """
    with immediate_txn(conn):
        pu = relay_db.gc_processed_updates(conn, older_than_days=processed_updates_days)
        pj = relay_db.gc_publication_jobs(conn, older_than_days=publication_jobs_days)
        rn = relay_db.gc_reply_notifications(conn, older_than_days=reply_notifications_days)
        tr = relay_db.gc_tweets_raw_payload(conn, older_than_days=tweets_raw_days)
        msg = relay_db.gc_messages(conn, older_than_days=messages_days)
        # MUST run after gc_messages: a freshly-orphaned chat (its last in-window
        # message just swept) is only eligible once those messages are gone.
        chats = relay_db.gc_orphan_chats(conn, messages_older_than_days=messages_days)
    result = RetentionGcResult(
        processed_updates=pu,
        publication_jobs=pj,
        reply_notifications=rn,
        tweets_raw_cleared=tr,
        messages=msg,
        chats=chats,
    )
    if any((pu, pj, rn, tr, msg, chats)):
        logger.info("relay sweeper retention GC: %s", result)
    return result


# ---------------------------------------------------------------------------
# 3. Stuck-claim reset (>5min)
# ---------------------------------------------------------------------------
def _recycle_or_kill_stuck_claim(conn: Connection, job: dict, *, source: str) -> str:
    """Recycle a stuck claim to ``retry``, or kill it ``dead`` once attempts exhausted.

    §15.6 "after N attempts, state=dead, alert admin" — the publisher's own
    send-failure path enforces ``attempts+1 >= MAX_ATTEMPTS → dead``; this mirrors
    that on the SWEEPER recycle path so a worker that repeatedly crashes mid-send
    (always re-claimed, never reaching the publisher's exception handler) cannot
    loop claimed→retry→claimed→retry forever. Returns ``'dead'`` or ``'retry'``.
    Must be called inside the caller's ``immediate_txn``.
    """
    attempts = int(job.get("attempts") or 0)
    if attempts + 1 >= MAX_ATTEMPTS:
        relay_db.kill_stuck_claim(
            conn,
            int(job["id"]),
            last_error=f"stuck claim attempts exhausted ({source}); marked dead by sweeper",
        )
        # §15.6 admin-alert hook: a quorum-reached job that exhausted its recycle
        # budget is a delivery failure operators must see.
        logger.error(
            "relay sweeper: job %s exhausted attempts on %s recycle — marked dead (alert admin)",
            job["id"],
            source,
        )
        return "dead"
    relay_db.reset_stuck_claim(conn, int(job["id"]))
    return "retry"


def reset_stuck_claims(conn: Connection, *, older_than_seconds: int = STUCK_CLAIM_SECONDS) -> int:
    """Recycle ``claimed`` jobs older than 5min back to ``retry`` (§3.1).

    Use :func:`reconcile_orphan_claims` instead when a :class:`Sender` is
    available — it closes the §3.2 duplicate window first. This is the
    no-reconciliation fallback (e.g. when no external-message search is wired):
    every stuck claim is recycled to ``retry`` so the publisher re-sends — EXCEPT
    a claim whose attempts are exhausted, which is killed ``dead`` (§15.6) rather
    than looping forever.
    """
    stuck = relay_db.list_stuck_claims(conn, older_than_seconds=older_than_seconds)
    reset = 0
    for job in stuck:
        with immediate_txn(conn):
            _recycle_or_kill_stuck_claim(conn, job, source="stuck-claim")
        reset += 1
    if reset:
        logger.info("relay sweeper: recycled %s stuck claim(s)", reset)
    return reset


# ---------------------------------------------------------------------------
# 4. Reconciliation (§3.2) — external effectively-once
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReconcileResult:
    """Outcome of one reconciliation pass."""

    claims_examined: int
    reconciled_done: int  # orphan external message found → recorded + marked done
    recycled_retry: int  # no orphan found → recycled to retry
    killed_dead: int = 0  # no orphan + attempts exhausted → killed dead (§15.6)


def reconcile_orphan_claims(
    conn: Connection,
    sender: Sender,
    *,
    older_than_seconds: int = RECONCILE_CLAIM_SECONDS,
) -> ReconcileResult:
    """Reconcile stuck ``claimed`` jobs before recycling them (§3.2).

    For each ``claimed`` job older than ``older_than_seconds``:
      1. **OUTSIDE any txn**: ``sender.find_recent_message(...)`` — best-effort
         search for a recent bot message matching the job's ``tweet_id``.
      2. If found, the crashed worker's ``send()`` had actually succeeded:
         ``immediate_txn`` → :func:`record_publication` (ON CONFLICT DO NOTHING)
         + :func:`reconcile_claim_done` (mark the job ``done``). This closes the
         §3.1 duplicate window — we do NOT re-send.
      3. If not found, ``immediate_txn`` → recycle to ``retry`` so the publisher
         re-sends — UNLESS attempts are exhausted, in which case the job is killed
         ``dead`` (§15.6 "after N attempts, state=dead, alert admin") instead of
         looping claimed→retry forever.

    The message-search is the ONLY external call and happens between
    transactions — never inside a ``BEGIN IMMEDIATE`` (the C2.2 invariant).
    """
    stuck = relay_db.list_stuck_claims(conn, older_than_seconds=older_than_seconds)
    reconciled = 0
    recycled = 0
    killed = 0
    for job in stuck:
        tweet = relay_db.get_tweet_by_row_id(conn, int(job["tweet_id"]))
        found_message_id: str | None = None
        try:
            found_message_id = sender.find_recent_message(
                destination_platform=job["destination_platform"],
                destination_chat_id=job["destination_chat_id"],
                tweet=tweet or {},
            )
        except Exception:  # pragma: no cover - best-effort; a search failure recycles
            logger.exception("relay reconcile: orphan search failed for job %s", job["id"])
            found_message_id = None

        if found_message_id:
            with immediate_txn(conn):
                relay_db.record_publication(
                    conn,
                    org_id=job["org_id"],
                    tweet_id=int(job["tweet_id"]),
                    destination_platform=job["destination_platform"],
                    destination_chat_id=job["destination_chat_id"],
                    destination_message_id=found_message_id,
                    submission_id=job.get("submission_id"),
                )
                relay_db.reconcile_claim_done(conn, int(job["id"]), destination_message_id=found_message_id)
            reconciled += 1
        else:
            with immediate_txn(conn):
                outcome = _recycle_or_kill_stuck_claim(conn, job, source="reconcile")
            if outcome == "dead":
                killed += 1
            else:
                recycled += 1

    if reconciled or recycled or killed:
        logger.info(
            "relay sweeper reconcile: %s reconciled-done, %s recycled-retry, %s killed-dead",
            reconciled,
            recycled,
            killed,
        )
    return ReconcileResult(
        claims_examined=len(stuck),
        reconciled_done=reconciled,
        recycled_retry=recycled,
        killed_dead=killed,
    )


# ---------------------------------------------------------------------------
# Full sweeper tick (orchestration)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SweepResult:
    """Outcome of one full sweeper tick."""

    submissions_expired: int
    retention: RetentionGcResult
    reconcile: ReconcileResult


def run_sweep(
    conn: Connection,
    sender: Sender,
) -> SweepResult:
    """Run one full sweeper tick: expiry → reconciliation → retention GC.

    Reconciliation runs BEFORE retention GC so a job that gets reconciled to
    ``done`` is then eligible for the 30d job-GC window on a later tick (not the
    same one — GC is by ``created_at``, conservatively in-window). Reconciliation
    subsumes the plain stuck-claim reset (it recycles to ``retry`` when no orphan
    message is found), so we do not also call :func:`reset_stuck_claims`.
    """
    expired = expire_submissions(conn)
    reconcile = reconcile_orphan_claims(conn, sender)
    retention = run_retention_gc(conn)
    return SweepResult(
        submissions_expired=expired,
        retention=retention,
        reconcile=reconcile,
    )


__all__ = [
    "expire_submissions",
    "run_retention_gc",
    "RetentionGcResult",
    "reset_stuck_claims",
    "reconcile_orphan_claims",
    "ReconcileResult",
    "run_sweep",
    "SweepResult",
    "STUCK_CLAIM_SECONDS",
    "RECONCILE_CLAIM_SECONDS",
]
