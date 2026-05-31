"""TG publisher (DESIGN §4 ``publisher/tg``) — the C3.6 outbox enqueue.

C3.6 is the SOLE owner of the ``[Approve]`` → publish enqueue. C3.5b only RECORDS
the operator decision + the ``final_text`` (the approved/edited reply) into
``autocm_drafts``/``autocm_reviews`` + the audit trail; it deliberately does NOT
touch the relay outbox. This module reads a C3.5b-approved draft and ENQUEUES
exactly ONE ``relay_publication_jobs`` row via
:func:`sable_platform.relay.db.enqueue_publication_job` — it NEVER calls a
transport (Telegram/Discord) directly. The C2.4 publisher
(``relay/feed/publisher.py``) drains the outbox and performs the actual send.

**The relay outbox is X-mirror-shaped** — ``relay_publication_jobs.tweet_id`` is a
``NOT NULL`` FK to ``relay_tweets``, and the C2.4 publisher resolves the body it
sends from ``relay_tweets.text`` (via ``get_tweet_by_row_id``). An AutoCM reply's
content is the draft's ``final_text``, which is NOT a real tweet. So C3.6
materializes ``final_text`` as a **synthetic ``relay_tweets`` carrier row** whose
``x_id`` is the deterministic, draft-derived handle :func:`carrier_x_id` (e.g.
``autocm-draft-42``). Because ``upsert_tweet`` is idempotent on the ``x_id`` UNIQUE
index, re-running over the same approved draft re-upserts the SAME carrier row →
the SAME ``tweet_id`` → the SAME ``(org_id, tweet_id, destination_platform,
destination_chat_id)`` dedupe key, so the C2.4 partial-unique dedupe collapses the
second enqueue to a no-op (returns ``None``).

**Idempotency is two-layered (defence in depth):**

  1. **Status guard** — :func:`publish_approved_draft` only enqueues when the draft
     is in status ``approved``. On a successful enqueue it flips the draft to
     ``published``, so a re-run sees ``published`` and short-circuits BEFORE
     touching the outbox at all.
  2. **Outbox dedupe** — even if the guard is bypassed (e.g. a concurrent re-run
     racing on the same ``approved`` row), the deterministic carrier ``x_id`` makes
     the second ``enqueue_publication_job`` hit the partial-unique
     ``relay_publication_jobs_dedupe`` index and return ``None`` (no second row).

The destination of the public reply is the SAME chat the inbound message came
from: resolved from the draft's ``source_message_id`` → ``relay_messages`` (chat,
platform, the external message id to reply to) → ``relay_chats`` (the external chat
id). When the source message / chat cannot be resolved the enqueue is skipped (no
destination → nothing to publish), reported on the result, and the draft is left
``approved`` for an operator to inspect.

Every successful enqueue records the SAFETY §5 audit field set
(``source_message_id``, cited ``chunk_ids``, ``draft_text``, ``final_text``,
reviewer, tier/category/confidence) on the audit trail AND stamps the outbox
``job_id`` / carrier ``tweet_id`` onto the draft-publish audit detail, so the
not-yet-sent enqueue is observable per SAFETY §5.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.db.audit import log_audit
from sable_platform.relay import db as relay_db

# log_audit verbs (audit-everything; source="sable-autocm").
AUDIT_SOURCE = "sable-autocm"
ACTION_PUBLISH_ENQUEUED = "publish_enqueued"

# The synthetic-carrier author handle stamped onto the relay_tweets row. NULO is
# the Sable community-agent persona; the carrier is the bot's own reply, not a
# mirrored third-party tweet, so the author is the bot itself.
CARRIER_AUTHOR_HANDLE = "nulo"

# Draft statuses the C3.6 publisher reads / writes (subset of the 058
# autocm_drafts CHECK set). ``approved`` is the C3.5b output; ``published`` is the
# terminal state this module flips to once the outbox enqueue lands.
STATUS_APPROVED = "approved"
STATUS_PUBLISHED = "published"


def _utc_now_iso() -> str:
    """UTC ISO-8601 ``...Z`` timestamp matching the autocm/relay TEXT columns."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def carrier_x_id(draft_id: int) -> str:
    """Deterministic synthetic ``relay_tweets.x_id`` for a draft's carrier row.

    Stable across re-runs (derived ONLY from the draft id) so ``upsert_tweet``
    returns the SAME ``tweet_id`` on a re-run — which is what makes the outbox
    ``relay_publication_jobs_dedupe`` partial-unique index collapse a double
    enqueue. Namespaced (``autocm-draft-``) so it never collides with a real X id.
    """
    return f"autocm-draft-{int(draft_id)}"


@dataclass(frozen=True)
class PublishEnqueueResult:
    """Outcome of a single :func:`publish_approved_draft` call.

    ``enqueued`` is ``True`` iff THIS call wrote a NEW ``relay_publication_jobs``
    row. ``job_id`` is that row id (``None`` when the enqueue was skipped or
    deduped). ``skipped_reason`` explains a no-op:

      * ``"not_found"``        — no such draft.
      * ``"not_approved"``     — the draft is not in status ``approved`` (already
                                 published / rejected / still pending) → no-op.
      * ``"no_final_text"``    — the approved draft has no resolvable final text.
      * ``"no_destination"``   — the source message / chat could not be resolved.
      * ``"already_enqueued"`` — the outbox dedupe collapsed this (a live job for
                                 the same (org, carrier, destination) already
                                 exists); idempotent re-run.
    """

    draft_id: int
    enqueued: bool
    job_id: Optional[int] = None
    tweet_id: Optional[int] = None
    org_id: Optional[str] = None
    skipped_reason: Optional[str] = None


def _load_approved_draft(conn: Connection, draft_id: int) -> Optional[dict]:
    row = conn.execute(
        text(
            "SELECT d.id, d.client_id, d.source_message_id, d.source_chat_id, "
            "       d.category, d.tier, d.register, d.draft_text, d.confidence, "
            "       d.cited_chunk_ids, d.status, c.org_id AS org_id "
            "FROM autocm_drafts d "
            "JOIN autocm_clients c ON c.id = d.client_id "
            "WHERE d.id = :d"
        ),
        {"d": draft_id},
    ).fetchone()
    return dict(row._mapping) if row is not None else None


def _latest_review(conn: Connection, draft_id: int) -> Optional[dict]:
    """The most-recent terminal review for a draft (the operator decision row).

    The C3.5b approve path inserts exactly one ``autocm_reviews`` row per draft;
    we still ``ORDER BY id DESC`` defensively so the LATEST decision wins.
    """
    row = conn.execute(
        text(
            "SELECT id, decision, edited_text, reviewer "
            "FROM autocm_reviews WHERE draft_id = :d "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"d": draft_id},
    ).fetchone()
    return dict(row._mapping) if row is not None else None


def _resolve_final_text(draft: dict, review: Optional[dict]) -> Optional[str]:
    """The reply text to publish — mirrors C3.5b's ``record_review_decision``.

      * ``edit``    → the operator's ``edited_text``;
      * ``approve`` → the unedited ``draft_text``.

    A ``reject`` / ``punt_to_founder`` decision never reaches here (the draft would
    not be in status ``approved``). When there is no review row at all we fall back
    to ``draft_text`` so a draft marked ``approved`` out-of-band still publishes its
    drafted body rather than silently dropping.
    """
    if review is not None and review.get("decision") == "edit":
        edited = review.get("edited_text")
        if edited:
            return str(edited)
    draft_text = draft.get("draft_text")
    return str(draft_text) if draft_text else None


def _resolve_destination(conn: Connection, draft: dict) -> Optional[dict]:
    """Resolve the public-reply destination from the draft's source message.

    The reply goes back to the SAME chat the inbound message arrived in. Joins
    ``relay_messages`` (the inbound row the draft answers) → ``relay_chats`` (the
    external chat-id surface) to produce ``{platform, destination_chat_id,
    reply_to}``. Returns ``None`` when the source message / chat is missing.
    """
    source_message_id = draft.get("source_message_id")
    if source_message_id is None:
        return None
    row = conn.execute(
        text(
            "SELECT m.platform AS platform, m.external_message_id AS reply_to, "
            "       ch.chat_id AS destination_chat_id "
            "FROM relay_messages m "
            "JOIN relay_chats ch ON ch.id = m.chat_id "
            "WHERE m.id = :m"
        ),
        {"m": int(source_message_id)},
    ).fetchone()
    if row is None:
        return None
    dest = dict(row._mapping)
    if not dest.get("platform") or not dest.get("destination_chat_id"):
        return None
    return dest


def _parse_cited(raw) -> list:
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return list(loaded) if isinstance(loaded, list) else []


def publish_approved_draft(
    conn: Connection,
    draft_id: int,
    *,
    commit: bool = True,
) -> PublishEnqueueResult:
    """Enqueue a C3.5b-approved draft onto the relay outbox (exactly once).

    The single load-bearing C3.6 behavior:

      1. Load the draft + its org. If it is NOT in status ``approved`` (already
         published, rejected, still pending, or missing) → no-op (the status guard
         — the first idempotency layer).
      2. Resolve the ``final_text`` (the operator's edited reply, or the drafted
         body) and the destination (the source message's chat + the message to
         reply to). A missing final text / destination is a reported skip.
      3. Materialize ``final_text`` as the synthetic ``relay_tweets`` carrier (a
         deterministic, draft-derived ``x_id`` so a re-run re-upserts the SAME
         carrier row → SAME ``tweet_id``).
      4. Enqueue exactly ONE ``relay_publication_jobs`` row via
         :func:`relay_db.enqueue_publication_job`. The partial-unique dedupe (the
         second idempotency layer) returns ``None`` if a live job already exists.
      5. On a fresh enqueue: flip the draft to ``published`` and write the SAFETY
         §5 audit field set + the outbox ``job_id`` / carrier ``tweet_id``.

    This function NEVER calls a transport directly — it only writes the outbox.
    The C2.4 publisher performs the actual platform send. ``commit`` is ``True`` by
    default (the caller usually owns a single approved draft per call); pass
    ``False`` to batch within an outer transaction.
    """
    draft = _load_approved_draft(conn, draft_id)
    if draft is None:
        return PublishEnqueueResult(draft_id=draft_id, enqueued=False, skipped_reason="not_found")

    org_id = draft.get("org_id")

    # --- 1. Status guard (idempotency layer #1) -----------------------------
    if draft.get("status") != STATUS_APPROVED:
        return PublishEnqueueResult(
            draft_id=draft_id,
            enqueued=False,
            org_id=org_id,
            skipped_reason="not_approved",
        )

    review = _latest_review(conn, draft_id)

    # --- 2. Resolve final text + destination --------------------------------
    final_text = _resolve_final_text(draft, review)
    if not final_text:
        return PublishEnqueueResult(
            draft_id=draft_id,
            enqueued=False,
            org_id=org_id,
            skipped_reason="no_final_text",
        )

    dest = _resolve_destination(conn, draft)
    if dest is None:
        return PublishEnqueueResult(
            draft_id=draft_id,
            enqueued=False,
            org_id=org_id,
            skipped_reason="no_destination",
        )

    # --- 3. Materialize the synthetic relay_tweets carrier ------------------
    # Idempotent on the x_id UNIQUE index: a re-run returns the SAME tweet_id, so
    # the outbox dedupe key is stable. raw carries the AutoCM provenance + the
    # reply-to so the C2.4 publisher can thread the reply.
    carrier_raw = json.dumps(
        {
            "autocm_draft_id": draft_id,
            "reply_to_external_message_id": dest.get("reply_to"),
        }
    )
    tweet_id = relay_db.upsert_tweet(
        conn,
        x_id=carrier_x_id(draft_id),
        x_author_handle=CARRIER_AUTHOR_HANDLE,
        text_body=final_text,
        is_reply=True,
        raw_json=carrier_raw,
    )

    # --- 4. Enqueue exactly ONE outbox row (idempotency layer #2) -----------
    job_id = relay_db.enqueue_publication_job(
        conn,
        org_id=org_id,
        tweet_id=tweet_id,
        destination_platform=dest["platform"],
        destination_chat_id=str(dest["destination_chat_id"]),
    )
    if job_id is None:
        # A live job for this (org, carrier, destination) already exists — the
        # outbox dedupe collapsed the re-run. Do NOT re-stamp / re-audit.
        if commit:
            conn.commit()
        return PublishEnqueueResult(
            draft_id=draft_id,
            enqueued=False,
            tweet_id=tweet_id,
            org_id=org_id,
            skipped_reason="already_enqueued",
        )

    # --- 5. Stamp the draft published + SAFETY §5 audit ---------------------
    conn.execute(
        text(
            "UPDATE autocm_drafts SET status = :s, resolved_at = :now WHERE id = :d"
        ),
        {"s": STATUS_PUBLISHED, "now": _utc_now_iso(), "d": draft_id},
    )

    cited = _parse_cited(draft.get("cited_chunk_ids"))
    reviewer = review.get("reviewer") if review is not None else None
    log_audit(
        conn,
        actor=reviewer or AUDIT_SOURCE,
        action=ACTION_PUBLISH_ENQUEUED,
        org_id=org_id,
        entity_id=str(draft_id),
        detail={
            "draft_id": draft_id,
            "job_id": job_id,
            "tweet_id": tweet_id,
            "destination_platform": dest["platform"],
            "destination_chat_id": str(dest["destination_chat_id"]),
            "reply_to_external_message_id": dest.get("reply_to"),
            "source_message_id": draft.get("source_message_id"),
            "chunk_ids": cited,
            "draft_text": draft.get("draft_text"),
            "final_text": final_text,
            "reviewer": reviewer,
            "category": draft.get("category"),
            "tier": draft.get("tier"),
            "confidence": draft.get("confidence"),
        },
        source=AUDIT_SOURCE,
    )

    if commit:
        conn.commit()

    return PublishEnqueueResult(
        draft_id=draft_id,
        enqueued=True,
        job_id=job_id,
        tweet_id=tweet_id,
        org_id=org_id,
    )


def publish_pending_approved(
    conn: Connection,
    *,
    org_id: Optional[str] = None,
    limit: int = 100,
) -> list[PublishEnqueueResult]:
    """Drain all currently-``approved`` drafts onto the outbox (one sweep).

    Reads every ``autocm_drafts`` row in status ``approved`` (optionally scoped to
    one ``org_id``) and enqueues each via :func:`publish_approved_draft`. Bounded by
    ``limit``. Each draft is independent — a skip on one (no destination, dedupe)
    does not abort the rest. Commits once at the end.
    """
    where = "WHERE d.status = :s"
    params: dict = {"s": STATUS_APPROVED, "lim": int(limit)}
    if org_id is not None:
        where += " AND c.org_id = :o"
        params["o"] = org_id
    rows = conn.execute(
        text(
            "SELECT d.id FROM autocm_drafts d "
            "JOIN autocm_clients c ON c.id = d.client_id "
            f"{where} ORDER BY d.id LIMIT :lim"
        ),
        params,
    ).fetchall()
    results = [
        publish_approved_draft(conn, int(r[0]), commit=False) for r in rows
    ]
    conn.commit()
    return results


# ---------------------------------------------------------------------------
# Transport seam (kept for interface continuity / the C2.7 typing-indicator
# helper) — C3.6 NEVER calls this for the public reply; it only writes the
# outbox. The C2.4 publisher owns the actual send.
# ---------------------------------------------------------------------------
class Publisher(Protocol):
    """Publish a reply to a chat via the SableRelay outbox (exactly-once)."""

    def publish(self, org_id: str, chat_id: str, text: str, *, reply_to: str | None = None) -> str:
        """Enqueue a reply on the relay outbox; return the outbox row handle."""
        ...


class NotImplementedTgPublisher:
    """Direct-transport stub — RETAINED to prove C3.6 never sends directly.

    C3.6 publishes ONLY by writing the relay outbox (see
    :func:`publish_approved_draft`); the C2.4 publisher performs the send. Any
    direct ``publish`` here raises, so a test can assert the direct path is never
    taken (the MEGAPLAN C3.6 "transport client never called directly" gate).
    """

    def publish(self, org_id: str, chat_id: str, text: str, *, reply_to: str | None = None) -> str:
        raise NotImplementedError(
            "C3.6 publishes via the relay outbox only — the transport send is the "
            "C2.4 publisher's job, never a direct call here"
        )


__all__ = [
    "PublishEnqueueResult",
    "carrier_x_id",
    "publish_approved_draft",
    "publish_pending_approved",
    "CARRIER_AUTHOR_HANDLE",
    "STATUS_APPROVED",
    "STATUS_PUBLISHED",
    "ACTION_PUBLISH_ENQUEUED",
    "AUDIT_SOURCE",
    "Publisher",
    "NotImplementedTgPublisher",
]
