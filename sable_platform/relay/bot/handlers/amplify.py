"""``/amplify`` — Flow B (operator quorum) + Flow C (shared immediate) (C2.3a).

Two entry points, both starting from ``/amplify <url> [note]`` (or an @bot
mention with a tweet URL), both role-gated, both running their DB writes inside
ONE ``immediate_txn`` with NO external API call inside it (the §3.1 / C2.2
invariant). The URL canonicalization + SocialData hydration (which IS an external
call) happens BEFORE the transaction, via the C2.4
:mod:`~sable_platform.relay.feed.canonical` helpers (§15.1).

  * **Flow B — operator submission with quorum** (:func:`amplify_operator`, PLAN
    §2 Flow B). An operator pastes/forwards a tweet into the OPERATOR chat. The
    handler records a ``pending`` ``relay_submissions`` row (with the quorum
    window materialized into ``expires_at``) and records the submitter's own
    quorum vote (the submitter counts as 1, §3). It returns a
    :class:`AmplifyResult` the listener uses to post a pending-acknowledgment
    ("📥 needs N more 📢 (1/T)") — and, since the control-message id is only known
    AFTER that send returns, the listener back-fills it via
    :func:`record_control_message` so the §3.1 reaction handler can route
    reactions to this submission. If the submitter's lone vote already meets the
    threshold (e.g. ``threshold=1``), the submission is transitioned + fanned out
    immediately in the same txn (single-operator clients).

  * **Flow C — team submission, shared chat, immediate** (:func:`amplify_shared`,
    PLAN §2 Flow C). A Sable operator OR client-team member runs ``/amplify`` in
    the SHARED chat. No quorum (single approval): the handler creates a
    ``ready_to_publish`` submission, marks it ``published``, and enqueues the
    fan-out — all in one txn. A client MAY opt into a ``shared_chat_threshold``
    gate (default unset/disabled, §2 Flow C / §3); when set, Flow C behaves like
    Flow B (records a pending submission + the submitter's vote and waits for
    quorum).

Per the LOCKED C2.1 §5.3 layering boundary, this module embeds NO raw SQL: every
statement is a named ``relay/db.py`` helper. Authorization is ALWAYS role-gated
via ``relay_member_roles`` (§8); the external ``user_id`` is the source of truth,
never the handle (§15.4).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.engine import Connection

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.handlers.quorum import (
    QuorumConfig,
    resolve_quorum_config,
)
from sable_platform.relay.bot.txn import immediate_txn
from sable_platform.relay.feed import canonical
from sable_platform.relay.feed.canonical import Hydrated, Rejection
from sable_platform.relay.socialdata import SocialDataClient

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expires_at(window_hours: int, *, now: datetime | None = None) -> str:
    """Materialize the quorum-window ``expires_at`` at insert (§5.2).

    Computed in Python and bound as a param (dialect-agnostic — Postgres has no
    ``datetime('now','+N hours')``). The sweeper expires pending submissions past
    this without cracking config JSON.
    """
    base = now or datetime.now(timezone.utc)
    return (base + timedelta(hours=window_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


# Machine-stable outcome codes (asserted by tests / used for the listener reply).
AMPLIFY_REJECTED = "rejected"  # URL/hydration rejected (§15.1) — nothing created
AMPLIFY_NOT_AUTHORIZED = "not_authorized"  # caller lacks the required role
AMPLIFY_PENDING = "pending"  # Flow B: pending submission opened, awaiting quorum
AMPLIFY_MERGED = "merged"  # duplicate of an existing open submission (§11 #2)
AMPLIFY_PUBLISHED = "published"  # Flow C immediate (or threshold==1) — fanned out


@dataclass(frozen=True)
class AmplifyResult:
    """Outcome of an ``/amplify`` invocation (drives the OUTSIDE-the-txn reply).

    ``code`` is one of the ``AMPLIFY_*`` constants. ``rejection`` carries the
    §15.1 precise reason when ``code == AMPLIFY_REJECTED`` (the listener echoes it
    in the source chat). For a pending submission the listener posts an
    acknowledgment and then calls :func:`record_control_message` with the sent
    message id. ``operator_count`` / ``threshold`` render "needs N more"; when
    ``published`` the publisher fans out the ``jobs_enqueued`` rows.
    """

    code: str
    submission_id: int | None = None
    org_id: str | None = None
    tweet_row_id: int | None = None
    x_id: str | None = None
    published: bool = False
    jobs_enqueued: int = 0
    operator_count: int = 0
    threshold: int = 0
    rejection: Rejection | None = None


def _hydrate(
    conn: Connection,
    client: SocialDataClient,
    org_id: str,
    raw_url: str,
) -> Hydrated | Rejection:
    """Canonicalize + hydrate a tweet URL (§15.1) — the EXTERNAL step, pre-txn.

    Returns :class:`~sable_platform.relay.feed.canonical.Hydrated` (the tweet was
    upserted into ``relay_tweets`` and the canonical ``x_id`` resolved) or a
    :class:`~sable_platform.relay.feed.canonical.Rejection` (no submission is
    created). The ``relay_tweets`` upsert inside ``hydrate_or_reject`` is a write,
    so the caller wraps THIS call in its own short ``immediate_txn``; the
    submission write then happens in a second txn after this returns. (Splitting
    keeps the SocialData call — inside ``hydrate_or_reject`` — out of the
    submission-write transaction, honoring §3.1.)
    """
    canon = canonical.canonicalize_tweet_url(raw_url)
    if isinstance(canon, Rejection):
        return canon
    return canonical.hydrate_or_reject(
        conn, client, org_id, canon.tweet_id, fallback_handle=canon.handle
    )


def _enqueue_fan_out(conn: Connection, *, org_id: str, tweet_row_id: int, submission_id: int) -> int:
    """Enqueue one publication job per active broadcast/community binding (§3.1 step 8)."""
    bindings = relay_db.list_active_publish_bindings(conn, org_id)
    enqueued = 0
    for b in bindings:
        job_id = relay_db.enqueue_publication_job(
            conn,
            org_id=org_id,
            tweet_id=tweet_row_id,
            destination_platform=b["platform"],
            destination_chat_id=b["chat_id"],
            submission_id=submission_id,
        )
        if job_id is not None:
            enqueued += 1
    return enqueued


def amplify_operator(
    conn: Connection,
    client: SocialDataClient,
    *,
    org_id: str,
    platform: str,
    submitter_external_user_id: str,
    source_chat_id: str,
    source_message_id: str,
    raw_url: str,
    note: str | None = None,
    submitter_handle: str | None = None,
    config: QuorumConfig | None = None,
) -> AmplifyResult:
    """Flow B: open an operator quorum submission for a pasted/forwarded tweet.

    Steps:
      1. Hydrate the URL (§15.1, EXTERNAL — its own short txn). A rejection
         creates NOTHING and returns the precise reason.
      2. Resolve/auto-create the submitter's member identity and ROLE-GATE: a
         non-operator caller is rejected (``AMPLIFY_NOT_AUTHORIZED``).
      3. Inside ONE ``immediate_txn``: one-pending-per-tweet MERGE (if an open
         submission for this tweet already exists, return ``AMPLIFY_MERGED`` —
         the §11 #2 merge), else create the ``pending`` submission (window
         materialized into ``expires_at``), record the submitter's own quorum vote
         (submitter counts as 1, §3), recompute the tally, and if the threshold is
         ALREADY met (single-operator clients with ``threshold=1`` /
         ``min_other_operators`` unset) transition + fan-out in the SAME txn.

    No external call happens inside the submission txn; the returned
    :class:`AmplifyResult` drives the listener's acknowledgment post.
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(f"unknown relay platform {platform!r}")

    # 1. Hydrate (external) — its own short txn so the SocialData call is not
    #    inside the submission-write transaction.
    with immediate_txn(conn):
        hydrated = _hydrate(conn, client, org_id, raw_url)
    if isinstance(hydrated, Rejection):
        return AmplifyResult(code=AMPLIFY_REJECTED, org_id=org_id, rejection=hydrated)

    cfg = config or resolve_quorum_config(conn, org_id)

    with immediate_txn(conn):
        # 2. Resolve/auto-create submitter identity; role-gate.
        submitter_id = relay_db.auto_create_member_identity(
            conn, platform, str(submitter_external_user_id), handle=submitter_handle
        )
        if not relay_db.is_relay_operator(conn, submitter_id, org_id):
            return AmplifyResult(
                code=AMPLIFY_NOT_AUTHORIZED, org_id=org_id, tweet_row_id=hydrated.tweet_row_id
            )

        # 3a. One-pending-per-tweet merge (§11 #2).
        existing = relay_db.find_open_submission_for_tweet(
            conn, org_id, hydrated.tweet_row_id
        )
        if existing is not None:
            # Record this operator's vote on the EXISTING submission (their
            # /amplify is a vote), then re-evaluate the threshold.
            relay_db.upsert_submission_reaction(
                conn, int(existing["id"]), submitter_id, cfg.emoji
            )
            return _maybe_resolve(
                conn,
                submission=existing,
                cfg=cfg,
                code=AMPLIFY_MERGED,
                tweet_row_id=hydrated.tweet_row_id,
                x_id=hydrated.x_id,
            )

        # 3b. Create the pending submission (window materialized into expires_at).
        submission_id = relay_db.create_submission(
            conn,
            org_id=org_id,
            tweet_id=hydrated.tweet_row_id,
            submitter_id=submitter_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            source_role="operator",
            expires_at=_expires_at(cfg.window_hours),
            note=note,
            status="pending",
        )
        # The submitter's /amplify IS a vote (submitter counts as 1, §3).
        relay_db.upsert_submission_reaction(conn, submission_id, submitter_id, cfg.emoji)

        submission = {
            "id": submission_id,
            "org_id": org_id,
            "tweet_id": hydrated.tweet_row_id,
            "submitter_id": submitter_id,
        }
        return _maybe_resolve(
            conn,
            submission=submission,
            cfg=cfg,
            code=AMPLIFY_PENDING,
            tweet_row_id=hydrated.tweet_row_id,
            x_id=hydrated.x_id,
        )


def _maybe_resolve(
    conn: Connection,
    *,
    submission: dict,
    cfg: QuorumConfig,
    code: str,
    tweet_row_id: int,
    x_id: str,
) -> AmplifyResult:
    """Recompute the tally and, if threshold is already met, transition + fan-out.

    Shared by the create + merge branches of Flow B. If the submitter's lone vote
    (plus any prior operator votes on a merged submission) already meets the
    threshold AND the optional ``min_other_operators`` constraint, the guarded
    transition runs and the fan-out is enqueued in the SAME txn (so a
    single-operator client's ``/amplify`` publishes immediately). Otherwise the
    submission stays ``pending`` and the caller posts the "needs N more"
    acknowledgment. Runs entirely inside the caller's ``immediate_txn``.
    """
    submission_id = int(submission["id"])
    org_id = submission["org_id"]
    count = relay_db.count_distinct_quorum_operators(conn, submission_id, org_id, cfg.emoji)

    meets_threshold = count >= cfg.threshold
    meets_min_other = True
    if cfg.min_other_operators is not None:
        others = relay_db.count_distinct_quorum_operators_excluding(
            conn, submission_id, org_id, cfg.emoji, int(submission["submitter_id"])
        )
        meets_min_other = others >= cfg.min_other_operators

    if meets_threshold and meets_min_other:
        transitioned = relay_db.transition_submission_ready(conn, submission_id)
        if transitioned:
            enqueued = _enqueue_fan_out(
                conn, org_id=org_id, tweet_row_id=tweet_row_id, submission_id=submission_id
            )
            return AmplifyResult(
                code=AMPLIFY_PUBLISHED,
                submission_id=submission_id,
                org_id=org_id,
                tweet_row_id=tweet_row_id,
                x_id=x_id,
                published=True,
                jobs_enqueued=enqueued,
                operator_count=count,
                threshold=cfg.threshold,
            )
        # A concurrent writer already transitioned (and fanned out) — do not
        # re-enqueue. Report as published; exactly-once holds.
        return AmplifyResult(
            code=AMPLIFY_PUBLISHED,
            submission_id=submission_id,
            org_id=org_id,
            tweet_row_id=tweet_row_id,
            x_id=x_id,
            published=True,
            jobs_enqueued=0,
            operator_count=count,
            threshold=cfg.threshold,
        )

    return AmplifyResult(
        code=code,
        submission_id=submission_id,
        org_id=org_id,
        tweet_row_id=tweet_row_id,
        x_id=x_id,
        operator_count=count,
        threshold=cfg.threshold,
    )


def amplify_shared(
    conn: Connection,
    client: SocialDataClient,
    *,
    org_id: str,
    platform: str,
    submitter_external_user_id: str,
    source_chat_id: str,
    source_message_id: str,
    raw_url: str,
    note: str | None = None,
    submitter_handle: str | None = None,
    config: QuorumConfig | None = None,
) -> AmplifyResult:
    """Flow C: shared-chat ``/amplify`` → immediate publish (single approval).

    Steps:
      1. Hydrate the URL (§15.1, EXTERNAL — its own short txn). A rejection
         creates NOTHING.
      2. Resolve/auto-create the submitter and ROLE-GATE: a Sable operator OR a
         client-team member may amplify in the shared chat (§8); anyone else is
         rejected.
      3. Inside ONE ``immediate_txn``: one-pending-per-tweet MERGE if an open
         submission already exists; else, if a ``shared_chat_threshold`` is
         configured, behave like Flow B (open a ``pending`` submission + record
         the submitter's vote and wait for quorum). With NO ``shared_chat_threshold``
         (the default), create a ``ready_to_publish`` submission, mark it
         ``published``, and enqueue the fan-out — immediate, no quorum.

    No external call inside the submission txn.
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(f"unknown relay platform {platform!r}")

    with immediate_txn(conn):
        hydrated = _hydrate(conn, client, org_id, raw_url)
    if isinstance(hydrated, Rejection):
        return AmplifyResult(code=AMPLIFY_REJECTED, org_id=org_id, rejection=hydrated)

    cfg = config or resolve_quorum_config(conn, org_id)

    with immediate_txn(conn):
        # 2. Resolve/auto-create submitter identity; role-gate (operator OR team).
        submitter_id = relay_db.auto_create_member_identity(
            conn, platform, str(submitter_external_user_id), handle=submitter_handle
        )
        is_operator = relay_db.is_relay_operator(conn, submitter_id, org_id)
        is_team = relay_db.member_has_role(conn, submitter_id, org_id, "client_team")
        if not (is_operator or is_team):
            return AmplifyResult(
                code=AMPLIFY_NOT_AUTHORIZED, org_id=org_id, tweet_row_id=hydrated.tweet_row_id
            )

        # 3a. One-pending-per-tweet merge.
        existing = relay_db.find_open_submission_for_tweet(
            conn, org_id, hydrated.tweet_row_id
        )
        if existing is not None:
            return AmplifyResult(
                code=AMPLIFY_MERGED,
                submission_id=int(existing["id"]),
                org_id=org_id,
                tweet_row_id=hydrated.tweet_row_id,
                x_id=hydrated.x_id,
            )

        # 3b. Optional shared-chat quorum gate (default disabled → immediate).
        if cfg.shared_chat_threshold is not None:
            submission_id = relay_db.create_submission(
                conn,
                org_id=org_id,
                tweet_id=hydrated.tweet_row_id,
                submitter_id=submitter_id,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
                source_role="shared",
                expires_at=_expires_at(cfg.window_hours),
                note=note,
                status="pending",
            )
            # Only an operator's vote counts toward the (operator) quorum; a
            # client-team submitter records the submission but does not self-vote
            # to threshold (the gate is operator peer-review, §3).
            if is_operator:
                relay_db.upsert_submission_reaction(
                    conn, submission_id, submitter_id, cfg.emoji
                )
            gate = QuorumConfig(
                threshold=cfg.shared_chat_threshold,
                emoji=cfg.emoji,
                window_hours=cfg.window_hours,
                min_other_operators=cfg.min_other_operators,
            )
            submission = {
                "id": submission_id,
                "org_id": org_id,
                "tweet_id": hydrated.tweet_row_id,
                "submitter_id": submitter_id,
            }
            return _maybe_resolve(
                conn,
                submission=submission,
                cfg=gate,
                code=AMPLIFY_PENDING,
                tweet_row_id=hydrated.tweet_row_id,
                x_id=hydrated.x_id,
            )

        # 3c. Default Flow C — immediate single-approval publish (no quorum).
        submission_id = relay_db.create_submission(
            conn,
            org_id=org_id,
            tweet_id=hydrated.tweet_row_id,
            submitter_id=submitter_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            source_role="shared",
            expires_at=_expires_at(cfg.window_hours),
            note=note,
            status="ready_to_publish",
        )
        enqueued = _enqueue_fan_out(
            conn, org_id=org_id, tweet_row_id=hydrated.tweet_row_id, submission_id=submission_id
        )
        relay_db.mark_submission_published(conn, submission_id)
        return AmplifyResult(
            code=AMPLIFY_PUBLISHED,
            submission_id=submission_id,
            org_id=org_id,
            tweet_row_id=hydrated.tweet_row_id,
            x_id=hydrated.x_id,
            published=True,
            jobs_enqueued=enqueued,
            threshold=cfg.threshold,
        )


def record_control_message(
    conn: Connection, submission_id: int, control_message_id: str
) -> None:
    """Back-fill a pending submission's ``control_message_id`` after the ack send.

    Flow B posts a pending acknowledgment into the operator chat; the message id
    is only known once that external send returns (OUTSIDE the submission txn).
    The listener then calls this (in a short follow-up ``immediate_txn``) so the
    §3.1 reaction handler can route reactions on that message to this submission
    via ``relay_submissions_control_lookup``.
    """
    with immediate_txn(conn):
        relay_db.set_submission_control_message_id(
            conn, int(submission_id), str(control_message_id)
        )


__all__ = [
    "AmplifyResult",
    "amplify_operator",
    "amplify_shared",
    "record_control_message",
    "AMPLIFY_REJECTED",
    "AMPLIFY_NOT_AUTHORIZED",
    "AMPLIFY_PENDING",
    "AMPLIFY_MERGED",
    "AMPLIFY_PUBLISHED",
]
