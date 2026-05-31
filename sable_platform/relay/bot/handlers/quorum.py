"""Flow B quorum: the §3.1 guarded transition + outbox enqueue (MEGAPLAN C2.3a).

This is the relay's single most concurrency-sensitive piece — the load-bearing
exactly-once enqueue. On a ``MessageReactionUpdated`` (TG) / reaction event
(Discord), the handler runs the ENTIRE §3.1 sequence inside ONE ``immediate_txn``
(``BEGIN IMMEDIATE`` on SQLite / SERIALIZABLE on Postgres — :mod:`relay.bot.txn`):

  1. Dedupe the update (``relay_processed_updates``; restart-safe). Duplicate →
     no-op.
  2. Resolve the submission via ``(source_chat_id, control_message_id)``. Unknown
     → no-op (reactions on a non-submission message — e.g. a Flow A broadcast —
     are NOT quorum signals, §2 Flow B note).
  3. Drop ANONYMOUS reactions (no ``user`` on the TG event — group-as-actor):
     quorum requires an identifiable operator (§3 / §15.4).
  4. Auto-create the member identity FOR AUDIT ONLY (grants no roles, §3.1 step
     4 / §8).
  5. Role-gate: a reactor who is NOT ``sable_operator`` (nor ``admin``) for the
     submission's org is DROPPED — the vote is not even recorded in
     ``relay_submission_reactions`` (§3.1 step 5 / §8 / §15.4: keep the audit
     table clean).
  6. Upsert / delete the reaction vote against the configured quorum emoji
     (added → upsert, removed → delete; other emoji are ignored for quorum).
  7. Recompute the distinct CURRENT-operator count for the submission.
  8. If the count ≥ ``quorum_threshold`` AND the optional ``min_other_operators``
     (operators OTHER than the submitter) constraint passes: the GUARDED
     transition ``UPDATE relay_submissions SET status='ready_to_publish' WHERE
     id=:id AND status='pending'`` — only the FIRST writer transitions (a
     concurrent writer sees ``status != 'pending'`` and the UPDATE matches zero
     rows). If and only if THIS call transitioned, enqueue the fan-out
     ``relay_publication_jobs`` rows (one per active broadcast/community binding).

**There is NO crash window between the state transition and the outbox enqueue**
— both are inside the same transaction (§3.1 "Crash window"). **No external API
call happens inside the transaction**: the handler returns a :class:`QuorumResult`
that the listener uses to drive the OUTSIDE-the-txn message edit
("✅ amplified …" / "📥 needs N more …").

Per the LOCKED C2.1 §5.3 layering boundary, this module embeds NO raw SQL: every
statement is a named, dialect-agnostic ``relay/db.py`` helper.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy.engine import Connection

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.dedupe import mark_processed
from sable_platform.relay.bot.txn import immediate_txn

logger = logging.getLogger(__name__)

# PLAN §3 defaults (each overridable per client via relay_clients.config.quorum).
DEFAULT_QUORUM_THRESHOLD = 2
DEFAULT_QUORUM_EMOJI = "\U0001F4E2"  # 📢
DEFAULT_QUORUM_WINDOW_HOURS = 24


@dataclass(frozen=True)
class QuorumConfig:
    """Resolved per-client quorum settings (PLAN §3 table / §6 config schema).

    Defaults match PLAN §3: ``threshold=2`` (distinct operators, submitter counts
    as 1), ``emoji='📢'``, ``window_hours=24``, ``min_other_operators=None``
    (disabled — when set, requires ≥N operators OTHER than the submitter on top of
    ``threshold``), ``shared_chat_threshold=None`` (Flow C is immediate unless a
    client opts into a shared-chat gate).
    """

    threshold: int = DEFAULT_QUORUM_THRESHOLD
    emoji: str = DEFAULT_QUORUM_EMOJI
    window_hours: int = DEFAULT_QUORUM_WINDOW_HOURS
    min_other_operators: int | None = None
    shared_chat_threshold: int | None = None


def _coerce_positive_int(value: object, default: int) -> int:
    """Coerce a config value to a positive int, else the default (tolerant parse)."""
    if isinstance(value, bool):  # bool is an int subclass — reject it explicitly
        return default
    if isinstance(value, int) and value > 0:
        return value
    return default


def _coerce_optional_positive_int(value: object) -> int | None:
    """Coerce a config value to a positive int or ``None`` (the disabled sentinel)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def resolve_quorum_config(conn: Connection, org_id: str) -> QuorumConfig:
    """Resolve a client's quorum settings from ``relay_clients.config.quorum``.

    Falls back to the PLAN §3 defaults for any absent / malformed field (a bad
    config never silently disables the gate — a missing/garbled ``threshold``
    defaults to 2, the conservative value). Reads the JSON via the
    ``read_client_config`` db helper (no raw SQL here).
    """
    raw = relay_db.read_client_config(conn, org_id)
    if not raw:
        return QuorumConfig()
    try:
        cfg = json.loads(raw)
    except (TypeError, ValueError):
        return QuorumConfig()
    quorum = cfg.get("quorum") if isinstance(cfg, dict) else None
    if not isinstance(quorum, dict):
        return QuorumConfig()
    emoji = quorum.get("emoji")
    return QuorumConfig(
        threshold=_coerce_positive_int(quorum.get("threshold"), DEFAULT_QUORUM_THRESHOLD),
        emoji=emoji if isinstance(emoji, str) and emoji else DEFAULT_QUORUM_EMOJI,
        window_hours=_coerce_positive_int(
            quorum.get("window_hours"), DEFAULT_QUORUM_WINDOW_HOURS
        ),
        min_other_operators=_coerce_optional_positive_int(quorum.get("min_other_operators")),
        shared_chat_threshold=_coerce_optional_positive_int(
            quorum.get("shared_chat_threshold")
        ),
    )


# Machine-stable outcome codes (asserted by tests / used for logging).
QUORUM_DUPLICATE = "duplicate"  # update already processed (dedupe)
QUORUM_UNKNOWN_SUBMISSION = "unknown_submission"  # reaction not on a submission
QUORUM_ANONYMOUS_DROPPED = "anonymous_dropped"  # no identifiable user
QUORUM_NON_OPERATOR_DROPPED = "non_operator_dropped"  # reactor lacks operator role
QUORUM_IGNORED_EMOJI = "ignored_emoji"  # reaction is not the quorum emoji
QUORUM_VOTE_RECORDED = "vote_recorded"  # vote up/removed, threshold not yet met
QUORUM_REACHED = "quorum_reached"  # this call transitioned + enqueued fan-out
QUORUM_ALREADY_RESOLVED = "already_resolved"  # threshold met but a prior writer won


@dataclass(frozen=True)
class QuorumResult:
    """Outcome of one reaction event (drives the OUTSIDE-the-txn message edit).

    ``code`` is one of the ``QUORUM_*`` constants. ``transitioned`` is ``True``
    ONLY for the single writer that performed the guarded ``pending →
    ready_to_publish`` transition this call (the exactly-once signal — the caller
    edits the control message to "✅ amplified …" and the publisher fans out the
    ``jobs_enqueued`` rows). ``operator_count`` / ``threshold`` /
    ``min_other_operators`` let the caller render "📥 needs N more 📢 (k/T)".
    """

    code: str
    submission_id: int | None = None
    org_id: str | None = None
    transitioned: bool = False
    jobs_enqueued: int = 0
    operator_count: int = 0
    threshold: int = DEFAULT_QUORUM_THRESHOLD
    min_other_operators: int | None = None


def _enqueue_fan_out(conn: Connection, submission: dict) -> int:
    """Enqueue one publication job per active broadcast/community binding (§3.1 step 8).

    Returns the count of jobs actually enqueued (the dedupe index collapses a
    repeat enqueue for an already in-flight (org, tweet, dest) to a no-op). MUST
    be called inside the SAME ``immediate_txn`` as the guarded transition so there
    is no crash window between the transition and the enqueue (§3.1).
    """
    bindings = relay_db.list_active_publish_bindings(conn, submission["org_id"])
    enqueued = 0
    for b in bindings:
        job_id = relay_db.enqueue_publication_job(
            conn,
            org_id=submission["org_id"],
            tweet_id=int(submission["tweet_id"]),
            destination_platform=b["platform"],
            destination_chat_id=b["chat_id"],
            submission_id=int(submission["id"]),
        )
        if job_id is not None:
            enqueued += 1
    return enqueued


def handle_reaction(
    conn: Connection,
    *,
    platform: str,
    update_id: object,
    source_chat_id: str,
    control_message_id: str,
    external_user_id: str | None,
    emoji_added: str | None = None,
    emoji_removed: str | None = None,
    handle: str | None = None,
    config: QuorumConfig | None = None,
) -> QuorumResult:
    """Process one reaction event through the §3.1 quorum sequence (one txn).

    ``external_user_id`` is ``None`` for an ANONYMOUS reaction (TG group-as-actor)
    — dropped per §3/§15.4. ``emoji_added`` / ``emoji_removed`` carry the diff of
    ``old_reaction`` vs ``new_reaction`` (the listener computes the delta against
    the configured quorum emoji and passes the relevant side); a reaction that is
    not the quorum emoji is ignored for quorum (but the listener may have logged
    it). The whole sequence runs inside ONE ``immediate_txn``; the returned
    :class:`QuorumResult` drives the OUTSIDE-the-txn control-message edit.

    ``config`` may be passed pre-resolved (the listener resolves it once); if
    ``None`` it is resolved from the submission's org inside the txn.
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(f"unknown relay platform {platform!r}")

    with immediate_txn(conn):
        # 1. Dedupe (restart-safe). A duplicate update is a no-op.
        is_new = mark_processed(conn, platform, update_id)
        if not is_new:
            return QuorumResult(code=QUORUM_DUPLICATE)

        # 2. Resolve the submission this reaction targets.
        submission = relay_db.find_submission_by_control(
            conn, source_chat_id, control_message_id
        )
        if submission is None:
            return QuorumResult(code=QUORUM_UNKNOWN_SUBMISSION)

        org_id = submission["org_id"]
        cfg = config or resolve_quorum_config(conn, org_id)

        # 3. Drop anonymous reactions — quorum requires an identifiable operator.
        if external_user_id is None:
            return QuorumResult(
                code=QUORUM_ANONYMOUS_DROPPED,
                submission_id=int(submission["id"]),
                org_id=org_id,
                threshold=cfg.threshold,
                min_other_operators=cfg.min_other_operators,
            )

        # 4. Auto-create the member identity FOR AUDIT ONLY (grants no roles).
        member_id = relay_db.auto_create_member_identity(
            conn, platform, str(external_user_id), handle=handle
        )

        # 5. Role-gate. A non-operator reaction is DROPPED — not recorded at all.
        if not relay_db.is_relay_operator(conn, member_id, org_id):
            return QuorumResult(
                code=QUORUM_NON_OPERATOR_DROPPED,
                submission_id=int(submission["id"]),
                org_id=org_id,
                threshold=cfg.threshold,
                min_other_operators=cfg.min_other_operators,
            )

        # 6. Upsert / delete the vote against the configured quorum emoji. A
        #    reaction that is not the quorum emoji is ignored for quorum.
        touched = False
        if emoji_added is not None and emoji_added == cfg.emoji:
            relay_db.upsert_submission_reaction(
                conn, int(submission["id"]), member_id, cfg.emoji
            )
            touched = True
        if emoji_removed is not None and emoji_removed == cfg.emoji:
            relay_db.delete_submission_reaction(
                conn, int(submission["id"]), member_id, cfg.emoji
            )
            touched = True
        if not touched:
            return QuorumResult(
                code=QUORUM_IGNORED_EMOJI,
                submission_id=int(submission["id"]),
                org_id=org_id,
                threshold=cfg.threshold,
                min_other_operators=cfg.min_other_operators,
            )

        # 7. Recompute the distinct CURRENT-operator tally.
        count = relay_db.count_distinct_quorum_operators(
            conn, int(submission["id"]), org_id, cfg.emoji
        )

        # 8. Threshold + optional min_other_operators gate.
        meets_threshold = count >= cfg.threshold
        meets_min_other = True
        if cfg.min_other_operators is not None:
            others = relay_db.count_distinct_quorum_operators_excluding(
                conn,
                int(submission["id"]),
                org_id,
                cfg.emoji,
                int(submission["submitter_id"]),
            )
            meets_min_other = others >= cfg.min_other_operators

        if not (meets_threshold and meets_min_other):
            # Threshold not yet met (or a vote was removed below threshold).
            return QuorumResult(
                code=QUORUM_VOTE_RECORDED,
                submission_id=int(submission["id"]),
                org_id=org_id,
                operator_count=count,
                threshold=cfg.threshold,
                min_other_operators=cfg.min_other_operators,
            )

        # Guarded transition — only the FIRST writer transitions.
        transitioned = relay_db.transition_submission_ready(conn, int(submission["id"]))
        if not transitioned:
            # A concurrent (or prior) writer already transitioned this submission;
            # the fan-out was enqueued exactly once by THAT writer. We must NOT
            # re-enqueue — exactly-once holds.
            return QuorumResult(
                code=QUORUM_ALREADY_RESOLVED,
                submission_id=int(submission["id"]),
                org_id=org_id,
                operator_count=count,
                threshold=cfg.threshold,
                min_other_operators=cfg.min_other_operators,
            )

        # THIS call won the transition → enqueue the fan-out (same txn).
        jobs_enqueued = _enqueue_fan_out(conn, submission)
        return QuorumResult(
            code=QUORUM_REACHED,
            submission_id=int(submission["id"]),
            org_id=org_id,
            transitioned=True,
            jobs_enqueued=jobs_enqueued,
            operator_count=count,
            threshold=cfg.threshold,
            min_other_operators=cfg.min_other_operators,
        )


__all__ = [
    "QuorumConfig",
    "QuorumResult",
    "resolve_quorum_config",
    "handle_reaction",
    "DEFAULT_QUORUM_THRESHOLD",
    "DEFAULT_QUORUM_EMOJI",
    "DEFAULT_QUORUM_WINDOW_HOURS",
    "QUORUM_DUPLICATE",
    "QUORUM_UNKNOWN_SUBMISSION",
    "QUORUM_ANONYMOUS_DROPPED",
    "QUORUM_NON_OPERATOR_DROPPED",
    "QUORUM_IGNORED_EMOJI",
    "QUORUM_VOTE_RECORDED",
    "QUORUM_REACHED",
    "QUORUM_ALREADY_RESOLVED",
]
