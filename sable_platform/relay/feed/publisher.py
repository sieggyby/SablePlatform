"""Outbox publisher — the §3.1 publish-exactly-once state machine (C2.4).

**Guarantee (PLAN §3.1):** *DB-exactly-once, external effectively-once with
reconciliation.* The publisher consumes ``relay_publication_jobs`` (the outbox
the listener's quorum transition enqueues) and drives each job through the
LOCKED §3.1 state set::

    pending ──claim──▶ claimed ──success──▶ done
       ▲                  │
       │                  ├── ratelimit/retryable ──▶ retry ──next_attempt_at──▶ (re-claim)
       │                  └── fatal / attempts≥MAX ──▶ dead
       │
       └── stuck-claim sweeper resets 'claimed' >5min → 'retry'  (lives in sweeper.py)

The load-bearing invariant (C2.2/§3.1 line 145, the audit/exit criterion):

    **The external send happens OUTSIDE any DB transaction.** The claim is one
    ``immediate_txn``; the send is between transactions; recording the
    publication is another ``immediate_txn``. No external API call ever happens
    inside a ``BEGIN IMMEDIATE``.

The crash window between ``send()`` returning success and the
``relay_publications`` insert is closed by the §3.2 reconciliation sweeper (in
``sweeper.py``), which best-effort-finds the orphan external message before
recycling a stuck claim.

**Publish-time re-hydration (PLAN §15.1 / §15.6).** Before sending, the publisher
re-hydrates the job's tweet via :func:`canonical.hydrate_or_reject` when a
:class:`SocialDataClient` seam is injected. A tweet deleted / suspended / made
private AFTER quorum/submission but BEFORE this claim hydrates to a
:class:`canonical.Rejection`: the publisher does NOT send, marks the job ``dead``,
marks the submission ``rejected`` (:func:`relay_db.reject_submission`) when a
``submission_id`` is present, and notifies the source chat via the optional
:class:`SourceNotifier` seam. This is the §15.6
"deleted-between-submit-and-publish → rejected" feed-integrity case. The
SocialData client + notifier are injected (faked in tests — NO real SocialData /
network), and the hydration call happens OUTSIDE any DB transaction (the §3.1
invariant). When no client is injected the gate is skipped (the cached tweet is
sent as before).

The sender is injectable (:class:`Sender` protocol) so tests drive a
deterministic fake — NO real Telegram/Discord/network call ever happens in
tests. The clock is injectable (``now`` / ``time_now``) so backoff timing is
deterministic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from sqlalchemy.engine import Connection

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot import binding as relay_binding
from sable_platform.relay.bot.txn import immediate_txn
from sable_platform.relay.feed import canonical
from sable_platform.relay.socialdata import SocialDataClient

logger = logging.getLogger(__name__)

# Max delivery attempts before a retryable error becomes terminal (``dead``).
# Distinct from the SocialData 429 retry budget — this is the destination-send
# attempt cap (§3.1 ``attempts≥MAX → dead``).
MAX_ATTEMPTS = 5

# Default backoff (seconds) for a retryable (non-ratelimit) send error when the
# error carries no explicit retry-after. Exponential over ``attempts``.
_BACKOFF_BASE_SECONDS = 4.0


# ---------------------------------------------------------------------------
# Send result + error taxonomy (the publisher classifies sender outcomes into
# the §3.1 transitions: done / retry / dead).
# ---------------------------------------------------------------------------
class SendRateLimited(Exception):
    """Destination rate-limited the send (TG 429 / Discord 429).

    Carries ``retry_after`` seconds (the publisher sets ``next_attempt_at`` to
    ``now + retry_after``, §3.1 ratelimit path). Never counts toward the
    attempts→dead budget; a rate limit is transient.
    """

    def __init__(self, message: str, *, retry_after: float = 30.0) -> None:
        super().__init__(message)
        self.retry_after = float(retry_after)


class SendRetryable(Exception):
    """A transient send failure (5xx, network blip). Retried with backoff.

    Becomes ``dead`` once ``attempts+1 >= MAX_ATTEMPTS`` (§3.1).
    """


class SendFatal(Exception):
    """A non-retryable send failure (e.g. malformed payload). Goes straight to ``dead``."""


class SendChannelGone(Exception):
    """Discord 403/404 — the channel was deleted / the bot lost access.

    Routed through :func:`~sable_platform.relay.bot.binding.flip_discord_binding_on_failure`:
    the per-destination consecutive-failure counter increments and, at the
    configured threshold, the binding flips to ``kicked`` (PLAN §15.3). Below the
    threshold it is treated as a retryable failure.
    """


@dataclass(frozen=True)
class SendOutcome:
    """What a :class:`Sender` returns on success — the external message id."""

    external_message_id: str


class Sender(Protocol):
    """The external-send seam (Telegram / Discord). Injected; faked in tests.

    ``send`` performs the actual platform API call and returns the external
    message id on success, or raises one of :class:`SendRateLimited` /
    :class:`SendRetryable` / :class:`SendFatal` / :class:`SendChannelGone`. It is
    called OUTSIDE any DB transaction (the §3.1 invariant).
    """

    def send(
        self,
        *,
        org_id: str,
        destination_platform: str,
        destination_chat_id: str,
        tweet: dict,
        submission_id: int | None,
    ) -> SendOutcome: ...

    def find_recent_message(
        self,
        *,
        destination_platform: str,
        destination_chat_id: str,
        tweet: dict,
    ) -> str | None:
        """Best-effort reconciliation search (§3.2): find an orphan external message.

        Returns the external message id of a recent bot message matching this
        job's ``tweet_id`` (embedded in the message metadata), or ``None`` if
        none is found. Called OUTSIDE any transaction by the reconciliation pass.
        """
        ...


class SourceNotifier(Protocol):
    """The §15.6 source-chat notify seam (faked in tests — NO real network).

    Called when a publish-time re-hydration rejects a tweet that was deleted /
    suspended / made private between submission and publish, to tell the
    submitter in the source chat. Best-effort: a notify failure must not abort
    the rejection (the job is already dead and the submission rejected).
    """

    def notify_rejected(
        self,
        *,
        org_id: str,
        source_chat_id: str | None,
        source_message_id: str | None,
        reason: str,
    ) -> None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _backoff_seconds(attempts: int) -> float:
    """Exponential backoff for a retryable send (§3.1), bounded at 1 hour."""
    return min(_BACKOFF_BASE_SECONDS * (2 ** max(0, attempts)), 3600.0)


@dataclass(frozen=True)
class PublishResult:
    """Outcome of a single :func:`publish_one_due_job` tick (for the loop/tests)."""

    job_id: int | None
    final_state: str | None  # 'done' / 'retry' / 'dead' / None (nothing due)
    published: bool  # True iff a NEW relay_publications row was written this tick
    external_message_id: str | None = None
    rejected: bool = False  # True iff the publish-time hydration gate rejected it


def _reject_at_publish(
    conn: Connection,
    job: dict,
    job_id: int,
    rejection: canonical.Rejection,
    *,
    source_notifier: SourceNotifier | None,
) -> PublishResult:
    """Handle a publish-time hydration rejection (§15.6): dead job, rejected submission, notify.

    No send happens. Marks the job ``dead`` (with the rejection reason) and, when
    the job carries a ``submission_id``, marks the submission ``rejected`` — both
    in ONE ``immediate_txn``. Then best-effort notifies the source chat via the
    optional :class:`SourceNotifier` (OUTSIDE the txn — it is an external call;
    its failure must not undo the DB rejection).
    """
    submission_id = job.get("submission_id")
    source_chat_id: str | None = None
    source_message_id: str | None = None
    with immediate_txn(conn):
        relay_db.mark_job_dead(
            conn, job_id, last_error=f"rejected on publish-hydration: {rejection.reason}"
        )
        if submission_id is not None:
            sub = relay_db.get_submission(conn, int(submission_id))
            if sub is not None:
                source_chat_id = sub.get("source_chat_id")
                source_message_id = sub.get("source_message_id")
            relay_db.reject_submission(
                conn, int(submission_id), reason=rejection.reason
            )
    logger.info(
        "relay publisher job %s rejected on publish-hydration (%s): %s",
        job_id,
        rejection.code,
        rejection.reason,
    )
    if source_notifier is not None:
        try:
            source_notifier.notify_rejected(
                org_id=job["org_id"],
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
                reason=rejection.reason,
            )
        except Exception:  # pragma: no cover - notify is best-effort
            logger.exception(
                "relay publisher: source-chat reject notify failed for job %s", job_id
            )
    return PublishResult(
        job_id=job_id, final_state="dead", published=False, rejected=True
    )


def publish_one_due_job(
    conn: Connection,
    sender: Sender,
    *,
    worker: str = "relay-publisher",
    now: Callable[[], datetime] = _now,
    sd_client: SocialDataClient | None = None,
    source_notifier: SourceNotifier | None = None,
) -> PublishResult:
    """Claim + publish ONE due job through the §3.1 state machine.

    Steps, with the txn boundaries that enforce "no external call inside a txn":

      1. ``immediate_txn``: :func:`claim_due_job` — flip oldest due
         ``pending``/``retry`` job to ``claimed``. (DB only.)
      2a. **OUTSIDE any txn** (when ``sd_client`` is injected): re-hydrate the
          tweet via :func:`canonical.hydrate_or_reject` (§15.1/§15.6). On a
          :class:`canonical.Rejection` (deleted/suspended/private between submit
          and publish): mark the job ``dead``, mark the submission ``rejected``,
          notify the source chat — and DO NOT send.
      2b. **OUTSIDE any txn**: ``sender.send(...)``. Classify the outcome.
      3. ``immediate_txn``: on success, :func:`record_publication` (ON CONFLICT
         DO NOTHING) + :func:`mark_job_done`; on ratelimit/retryable/fatal, the
         matching state transition. (DB only.)

    Returns a :class:`PublishResult`. ``final_state=None`` means nothing was due.
    """
    # --- 1. Claim (DB only) ---
    with immediate_txn(conn):
        job = relay_db.claim_due_job(conn, worker)
    if job is None:
        return PublishResult(job_id=None, final_state=None, published=False)

    job_id = int(job["id"])
    tweet = relay_db.get_tweet_by_row_id(conn, int(job["tweet_id"]))

    # The tweet SELECT above leaves a read-only autobegin open under SA 2.0; clear
    # it so the external send below runs with NO transaction held at all — the
    # §3.1 invariant is "no external API call inside a transaction", and a
    # read-only autobegin discards nothing durable on rollback.
    if conn.in_transaction():
        conn.rollback()

    # --- 2a. Publish-time re-hydration gate (§15.1/§15.6) ---
    # A tweet deleted/suspended/made-private AFTER quorum but BEFORE this claim is
    # rejected here — it is NOT broadcast. Skipped when no SocialData client is
    # injected (the cached tweet is sent as-is). The hydrate call is an external
    # SocialData fetch and happens OUTSIDE any txn (the §3.1 invariant). The
    # tweet's canonical x_id is the row's x_id (never the URL, §15.1).
    if sd_client is not None and tweet is not None:
        hydrate_id = str(tweet.get("x_id") or "")
        if hydrate_id:
            hydrated = canonical.hydrate_or_reject(
                conn, sd_client, job["org_id"], hydrate_id
            )
            # hydrate_or_reject's upsert opens an autobegin; clear it before the
            # rejection writes (which open their own immediate_txn) or the send.
            if conn.in_transaction():
                conn.rollback()
            if isinstance(hydrated, canonical.Rejection):
                return _reject_at_publish(
                    conn, job, job_id, hydrated, source_notifier=source_notifier
                )

    # --- 2b. External send (OUTSIDE any transaction — the §3.1 invariant) ---
    try:
        outcome = sender.send(
            org_id=job["org_id"],
            destination_platform=job["destination_platform"],
            destination_chat_id=job["destination_chat_id"],
            tweet=tweet or {},
            submission_id=job.get("submission_id"),
        )
    except SendRateLimited as exc:
        with immediate_txn(conn):
            relay_db.mark_job_retry(
                conn,
                job_id,
                retry_after_seconds=exc.retry_after,
                last_error=f"ratelimited: {exc}",
                now=now(),
            )
        logger.info("relay publisher job %s ratelimited; retry in %ss", job_id, exc.retry_after)
        return PublishResult(job_id=job_id, final_state="retry", published=False)
    except SendChannelGone as exc:
        # Discord 403/404 — increment the per-destination consecutive-failure
        # counter and let binding.flip_discord_binding_on_failure decide whether
        # the binding flips to kicked. The failure-flip runs in its own txn
        # inside that helper; we never call it inside our own txn. Below the
        # threshold, treat as retryable so the publisher keeps trying.
        attempts = int(job.get("attempts") or 0) + 1
        flip = relay_binding.flip_discord_binding_on_failure(
            conn, job["org_id"], job["destination_chat_id"], attempts
        )
        if flip.flipped:
            # The binding flip already killed in-flight jobs (state='dead').
            logger.warning(
                "relay publisher job %s: discord channel gone, binding flipped kicked",
                job_id,
            )
            return PublishResult(job_id=job_id, final_state="dead", published=False)
        with immediate_txn(conn):
            relay_db.mark_job_retry(
                conn,
                job_id,
                retry_after_seconds=_backoff_seconds(attempts),
                last_error=f"channel gone (attempt {attempts}): {exc}",
                now=now(),
            )
        return PublishResult(job_id=job_id, final_state="retry", published=False)
    except SendRetryable as exc:
        attempts = int(job.get("attempts") or 0) + 1
        with immediate_txn(conn):
            if attempts >= MAX_ATTEMPTS:
                relay_db.mark_job_dead(conn, job_id, last_error=f"retryable exhausted: {exc}")
                final = "dead"
            else:
                relay_db.mark_job_retry(
                    conn,
                    job_id,
                    retry_after_seconds=_backoff_seconds(attempts),
                    last_error=f"retryable (attempt {attempts}): {exc}",
                    now=now(),
                )
                final = "retry"
        return PublishResult(job_id=job_id, final_state=final, published=False)
    except SendFatal as exc:
        with immediate_txn(conn):
            relay_db.mark_job_dead(conn, job_id, last_error=f"fatal: {exc}")
        return PublishResult(job_id=job_id, final_state="dead", published=False)

    # --- 3. Record publication + mark done (DB only) ---
    with immediate_txn(conn):
        wrote = relay_db.record_publication(
            conn,
            org_id=job["org_id"],
            tweet_id=int(job["tweet_id"]),
            destination_platform=job["destination_platform"],
            destination_chat_id=job["destination_chat_id"],
            destination_message_id=outcome.external_message_id,
            submission_id=job.get("submission_id"),
        )
        relay_db.mark_job_done(conn, job_id)
    return PublishResult(
        job_id=job_id,
        final_state="done",
        published=wrote,
        external_message_id=outcome.external_message_id,
    )


def drain_due_jobs(
    conn: Connection,
    sender: Sender,
    *,
    worker: str = "relay-publisher",
    now: Callable[[], datetime] = _now,
    max_jobs: int = 100,
    sd_client: SocialDataClient | None = None,
    source_notifier: SourceNotifier | None = None,
) -> list[PublishResult]:
    """Publish all currently-due jobs (one loop tick); bounded by ``max_jobs``.

    Stops when no job is due (or ``max_jobs`` is reached). Each job is an
    independent claim→hydrate→send→record cycle; a failure on one does not abort
    the rest. The optional ``sd_client`` / ``source_notifier`` seams enable the
    §15.1/§15.6 publish-time re-hydration gate. Returns the per-job results in
    order.
    """
    results: list[PublishResult] = []
    for _ in range(max_jobs):
        result = publish_one_due_job(
            conn,
            sender,
            worker=worker,
            now=now,
            sd_client=sd_client,
            source_notifier=source_notifier,
        )
        if result.final_state is None:
            break
        results.append(result)
    return results


__all__ = [
    "Sender",
    "SourceNotifier",
    "SendOutcome",
    "SendRateLimited",
    "SendRetryable",
    "SendFatal",
    "SendChannelGone",
    "PublishResult",
    "publish_one_due_job",
    "drain_due_jobs",
    "MAX_ATTEMPTS",
]
