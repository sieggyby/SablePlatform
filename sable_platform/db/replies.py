"""Operator reply-suggestion CRUD (migration 056).

Backs the SableWeb /ops reply-suggestion feature. SablePlatform owns these
tables; Slopper reaches them through this in-process helper (NOT by writing
sable.db directly, and NOT via a per-request CLI fork — see
Sable_Slopper/docs/OPERATOR_REPLY_WEB_FEATURE.md §6).

Three concerns:
- reserve_generation / refund_generation: the persistent per-(operator, UTC-day)
  generation quota. Reserve-before-spend: increment first, refund if the
  generation later fails, so a failed call doesn't burn quota.
- log_suggestion: append a generation to the audit log.
- record_outcome: idempotently map an actual posted reply back to a suggestion
  for assisted-vs-organic lift measurement.

Platform stores and queries — it does not interpret. Callers own the shape of
``variants_json`` / ``engagement_json`` and are responsible for committing the
transaction (these helpers do not commit, matching the repo convention).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

DEFAULT_DAILY_LIMIT = 50


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _day_utc(now: datetime | None = None) -> str:
    """The UTC calendar day key (YYYY-MM-DD). Single source of truth."""
    return (now or _utc_now()).strftime("%Y-%m-%d")


def _resets_at(now: datetime | None = None) -> str:
    """ISO8601 Z timestamp of the next UTC midnight (when the quota resets)."""
    n = now or _utc_now()
    nxt = (n + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Reservation(NamedTuple):
    allowed: bool
    remaining: int
    resets_at: str
    used: int


def reserve_generation(
    conn: Connection,
    operator_handle: str,
    *,
    limit: int = DEFAULT_DAILY_LIMIT,
    org_id: str | None = None,
    now: datetime | None = None,
) -> Reservation:
    """Atomically reserve one generation against today's per-operator quota.

    Single-statement upsert (ON CONFLICT ... RETURNING) so concurrent requests
    cannot both pass a stale read (no TOCTOU). If the reservation pushes the
    count past ``limit`` it is refunded immediately and ``allowed`` is False.

    The caller MUST commit. On a downstream generation failure the caller
    should call :func:`refund_generation` to release the reserved slot.
    """
    n = now or _utc_now()
    day = _day_utc(n)
    stamp = n.strftime("%Y-%m-%dT%H:%M:%SZ")

    row = conn.execute(
        text(
            "INSERT INTO operator_reply_quota"
            " (operator_handle, day_utc, org_id, count, updated_at)"
            " VALUES (:h, :d, :org, 1, :now)"
            " ON CONFLICT(operator_handle, day_utc) DO UPDATE SET"
            " count = operator_reply_quota.count + 1, updated_at = :now"
            " RETURNING count"
        ),
        {"h": operator_handle, "d": day, "org": org_id, "now": stamp},
    ).fetchone()
    new_count = int(row[0])

    if new_count > limit:
        # Over the cap — release the slot we just took so the stored count
        # reflects only granted reservations.
        conn.execute(
            text(
                "UPDATE operator_reply_quota SET count = count - 1, updated_at = :now"
                " WHERE operator_handle = :h AND day_utc = :d"
            ),
            {"h": operator_handle, "d": day, "now": stamp},
        )
        return Reservation(False, 0, _resets_at(n), limit)

    return Reservation(True, max(0, limit - new_count), _resets_at(n), new_count)


def refund_generation(
    conn: Connection,
    operator_handle: str,
    *,
    now: datetime | None = None,
) -> None:
    """Release a previously-reserved slot (call when the generation failed).

    Never drives the counter below zero. Caller commits.
    """
    n = now or _utc_now()
    conn.execute(
        text(
            "UPDATE operator_reply_quota SET count = count - 1,"
            " updated_at = :now"
            " WHERE operator_handle = :h AND day_utc = :d AND count > 0"
        ),
        {"h": operator_handle, "d": _day_utc(n), "now": n.strftime("%Y-%m-%dT%H:%M:%SZ")},
    )


def get_quota(
    conn: Connection,
    operator_handle: str,
    *,
    limit: int = DEFAULT_DAILY_LIMIT,
    now: datetime | None = None,
) -> Reservation:
    """Read today's quota without consuming a slot."""
    n = now or _utc_now()
    row = conn.execute(
        text(
            "SELECT count FROM operator_reply_quota"
            " WHERE operator_handle = :h AND day_utc = :d"
        ),
        {"h": operator_handle, "d": _day_utc(n)},
    ).fetchone()
    used = int(row[0]) if row else 0
    return Reservation(used < limit, max(0, limit - used), _resets_at(n), used)


def log_suggestion(
    conn: Connection,
    *,
    operator_handle: str,
    org_id: str,
    source_tweet_id: str,
    variants: list[dict[str, Any]],
    source_author: str | None = None,
    source_text: str | None = None,
    model: str | None = None,
    cost_usd: float | None = None,
    clip_media_kind: str | None = None,
    opportunity_id: int | None = None,
    source_conversation_id: str | None = None,
    now: datetime | None = None,
) -> str:
    """Append a generation to the suggestion log. Returns the suggestion id.

    ``cost_usd`` is stored here for internal accounting only — it must never be
    returned to the browser (see the data-exposure rules in the design doc).
    ``clip_media_kind`` ('image' | 'video' | None) records the media kind the
    reply attached, for the prefer-image throttle.

    ``opportunity_id`` (mig 062, INTEGER) is the learning join back to the
    reply-opportunity feed row this generation came from (NULL for a paste-URL
    generation with no feed origin). ``source_conversation_id`` (mig 062, TEXT)
    is the target tweet's ``conversation_id`` stamped at generation time so the
    sweep's depress-already-replied is a LOCAL lookup (no SocialData call) — see
    :func:`conversation_already_replied`. Both default to ``None`` so existing
    callers are unaffected. Caller commits.
    """
    sid = uuid.uuid4().hex
    stamp = (now or _utc_now()).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        text(
            "INSERT INTO reply_suggestions"
            " (id, operator_handle, org_id, source_tweet_id, source_author,"
            "  source_text, variants_json, model, cost_usd, clip_media_kind,"
            "  opportunity_id, source_conversation_id, generated_at)"
            " VALUES (:id, :h, :org, :tid, :author, :stext, :vj, :model, :cost, :ck,"
            "  :oid, :scid, :now)"
        ),
        {
            "id": sid,
            "h": operator_handle,
            "org": org_id,
            "tid": source_tweet_id,
            "author": source_author,
            "stext": source_text,
            "vj": json.dumps(variants),
            "model": model,
            "cost": cost_usd,
            "ck": clip_media_kind,
            "oid": None if opportunity_id is None else int(opportunity_id),
            "scid": source_conversation_id,
            "now": stamp,
        },
    )
    return sid


def conversation_already_replied(
    conn: Connection, org_id: str, conversation_id: str
) -> bool:
    """True iff this org has already logged a reply in ``conversation_id`` (mig 062).

    A purely LOCAL lookup over ``reply_suggestions.source_conversation_id`` — the
    cheap depress-already-replied signal the sweep uses to drop candidates whose
    conversation an operator already replied to *through* reply-assist, with NO
    per-candidate SocialData call (plan §4.4 / §9). Org-scoped, so one client's
    replies never depress another's. Read-only.
    """
    if conversation_id is None:
        return False
    row = conn.execute(
        text(
            "SELECT 1 FROM reply_suggestions"
            " WHERE org_id = :org AND source_conversation_id = :cid"
            " LIMIT 1"
        ),
        {"org": org_id, "cid": conversation_id},
    ).fetchone()
    return row is not None


def count_image_recs(
    conn: Connection,
    operator_handle: str,
    *,
    days: int = 7,
    now: datetime | None = None,
) -> int:
    """How many image clips this operator was recommended in the last ``days``.

    Backs the anti-spam image throttle: when this reaches the threshold the
    system stops *auto*-preferring images (an explicit operator request still
    attaches one). ``generated_at`` is ISO-8601-Z TEXT, lexically comparable.
    """
    cutoff = ((now or _utc_now()) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute(
        text(
            "SELECT COUNT(*) AS n FROM reply_suggestions"
            " WHERE operator_handle = :h AND clip_media_kind = 'image'"
            "   AND generated_at >= :cutoff"
        ),
        {"h": operator_handle, "cutoff": cutoff},
    ).fetchone()
    return int(row[0] if row is not None else 0)


def record_outcome(
    conn: Connection,
    *,
    suggestion_id: str,
    posted_tweet_id: str,
    posted_at: str | None = None,
    chosen_variant_idx: int | None = None,
    was_edited: bool = False,
    engagement: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """Idempotently map an actual posted reply to a suggestion (lift tracking).

    Idempotent on (suggestion_id, posted_tweet_id) via the unique index, so the
    reconciliation job may re-run safely. Returns True if a new row was written,
    False if it already existed. Caller commits.
    """
    stamp = (now or _utc_now()).strftime("%Y-%m-%dT%H:%M:%SZ")
    existing = conn.execute(
        text(
            "SELECT 1 FROM reply_outcomes"
            " WHERE suggestion_id = :sid AND posted_tweet_id = :ptid"
        ),
        {"sid": suggestion_id, "ptid": posted_tweet_id},
    ).fetchone()
    if existing:
        # Update engagement on re-reconciliation (the metrics drift as the
        # tweet ages) without creating a duplicate row.
        conn.execute(
            text(
                "UPDATE reply_outcomes SET engagement_json = :ej, posted_at = COALESCE(:pat, posted_at)"
                " WHERE suggestion_id = :sid AND posted_tweet_id = :ptid"
            ),
            {"ej": json.dumps(engagement or {}), "pat": posted_at, "sid": suggestion_id, "ptid": posted_tweet_id},
        )
        return False

    # The unique index still guards against a concurrent racer between the
    # SELECT and the INSERT — DO NOTHING keeps that case a safe no-op.
    conn.execute(
        text(
            "INSERT INTO reply_outcomes"
            " (id, suggestion_id, posted_tweet_id, posted_at, chosen_variant_idx,"
            "  was_edited, engagement_json, recorded_at)"
            " VALUES (:id, :sid, :ptid, :pat, :idx, :edited, :ej, :now)"
            " ON CONFLICT(suggestion_id, posted_tweet_id) DO NOTHING"
        ),
        {
            "id": uuid.uuid4().hex,
            "sid": suggestion_id,
            "ptid": posted_tweet_id,
            "pat": posted_at,
            "idx": chosen_variant_idx,
            "edited": 1 if was_edited else 0,
            "ej": json.dumps(engagement or {}),
            "now": stamp,
        },
    )
    return True


def find_suggestion(
    conn: Connection,
    operator_handle: str,
    source_tweet_id: str,
) -> tuple[str, list[Any]] | None:
    """Most recent logged suggestion for (operator, source tweet), or None.

    Used by the reconciliation matcher to decide whether an operator's reply
    was assisted (a suggestion existed) and which variant they used.
    """
    row = conn.execute(
        text(
            "SELECT id, variants_json FROM reply_suggestions"
            " WHERE operator_handle = :h AND source_tweet_id = :t"
            " ORDER BY generated_at DESC LIMIT 1"
        ),
        {"h": operator_handle, "t": source_tweet_id},
    ).fetchone()
    if not row:
        return None
    try:
        variants = json.loads(row[1]) if row[1] else []
    except (json.JSONDecodeError, TypeError):
        variants = []
    return (str(row[0]), variants)


def get_outcomes_summary(conn: Connection, org_id: str) -> dict[str, Any]:
    """Persisted assisted-reply rollup for an org (for the /ops lift view).

    Reads recorded outcomes joined to their suggestions. Engagement is read
    from ``engagement_json.total``. Adoption = a suggested variant was used
    unedited. This covers the *assisted* side only; the live organic baseline
    is computed during reconciliation (the timeline isn't persisted here).
    """
    rows = conn.execute(
        text(
            "SELECT o.was_edited, o.chosen_variant_idx, o.engagement_json"
            " FROM reply_outcomes o JOIN reply_suggestions s ON o.suggestion_id = s.id"
            " WHERE s.org_id = :org"
        ),
        {"org": org_id},
    ).fetchall()

    n = len(rows)
    if n == 0:
        return {"assisted_count": 0, "adopted_count": 0, "adoption_rate": 0.0, "mean_engagement": 0.0}

    adopted = 0
    engagement_total = 0.0
    # NB: index rows positionally — CompatConnection's Row yields column *names*
    # when iterated/unpacked, but values via [i].
    for r in rows:
        was_edited, chosen_idx, ej = r[0], r[1], r[2]
        if chosen_idx is not None and not was_edited:
            adopted += 1
        try:
            engagement_total += float((json.loads(ej) if ej else {}).get("total", 0) or 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    return {
        "assisted_count": n,
        "adopted_count": adopted,
        "adoption_rate": round(adopted / n, 3),
        "mean_engagement": round(engagement_total / n, 2),
    }


def count_replies_delivered(
    conn: Connection, org_id: str, since: str | None = None, until: str | None = None
) -> int:
    """Count delivered (posted) reply outcomes for an org over an optional window.

    org_id / operator live on ``reply_suggestions``; an outcome row *is* a posted
    reply (``posted_tweet_id NOT NULL``), so COUNT(rows) = posts delivered (a
    single suggestion can yield multiple posts). ``posted_at`` is nullable, so we
    window on ``COALESCE(posted_at, recorded_at)`` to avoid silently dropping
    timestamp-less outcomes from this *measured* count. Used by the work-tracking
    rollup (``work_tracking.get_work_summary``).
    """
    sql = (
        "SELECT COUNT(*) FROM reply_outcomes o"
        " JOIN reply_suggestions s ON o.suggestion_id = s.id"
        " WHERE s.org_id = :org"
    )
    params: dict[str, Any] = {"org": org_id}
    if since is not None:
        sql += " AND COALESCE(o.posted_at, o.recorded_at) >= :since"
        params["since"] = since
    if until is not None:
        sql += " AND COALESCE(o.posted_at, o.recorded_at) < :until"
        params["until"] = until
    row = conn.execute(text(sql), params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0
