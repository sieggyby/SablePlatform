"""Conversation Watcher flag CRUD (mig 086) — the durable record + dedupe/cooldown substrate
+ feedback ledger for community-chat conversation flags.

A flag is one moment in a moderated community chat (Discord/Telegram) that a zero-LLM
heuristic scorer judged worth an operator pitching into. SablePlatform owns the table; the
Slopper ``sable/watch/`` module owns the detection logic and calls these accessors.

Two invariants worth stating:

  * DEDUPE IS APP-LEVEL, not a DB UNIQUE. ``insert_flag`` refuses to write when a
    non-terminal flag already exists for the same ``(platform, channel_id, kind)`` inside a
    cooldown window — one flag per channel per burst, however hot the channel gets. The
    check + insert must run inside the caller's ``immediate_txn`` so two watcher ticks can't
    both write. This mirrors ``relay/db.py`` ``find_active_opportunity_for_tweet`` /
    ``upsert_sweep_opportunity``.

  * ``brand_risk`` flags carry their own, shorter cooldown and bypass the burst gate at the
    scorer — but they are still deduped here (a member repeating a forbidden claim in a
    minute is one flag, not ten).

Writers need the caller's ``immediate_txn`` and must commit. Reads are transaction-free.
No cost column — the scorer is zero-LLM by design.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text as _sa_text
from sqlalchemy.engine import Connection

_COLS = (
    "id, org_id, platform, space_id, channel_id, anchor_message_id, "
    "window_start, window_end, score, kind, signals_json, reason, status, "
    "feedback, delivered_at, expires_at, created_at"
)

_TERMINAL = ("handled", "noise", "expired")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_active_flag_in_window(
    conn: Connection,
    *,
    platform: str,
    channel_id: str,
    kind: str,
    cooldown_minutes: int,
    now: str | None = None,
) -> dict | None:
    """The most recent NON-TERMINAL flag of this ``kind`` in this channel whose ``created_at``
    is within ``cooldown_minutes`` of ``now`` — the dedupe/cooldown probe. None if the channel
    is clear to flag again. Read-only, but call it inside the same txn as the insert.

    A delivered-but-not-yet-adjudicated flag ('active'/'delivered') still suppresses; a
    terminal flag ('handled'/'noise'/'expired') does not (the conversation moved on)."""
    now = now or _utc_now_iso()
    cutoff = (
        datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        - timedelta(minutes=cooldown_minutes)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute(
        _sa_text(
            f"SELECT {_COLS} FROM community_conversation_flags "
            "WHERE platform = :p AND channel_id = :ch AND kind = :k "
            "  AND status NOT IN ('handled', 'noise', 'expired') "
            "  AND created_at >= :cutoff "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"p": platform, "ch": str(channel_id), "k": kind, "cutoff": cutoff},
    ).fetchone()
    return dict(row._mapping) if row is not None else None


def insert_flag(
    conn: Connection,
    *,
    org_id: str,
    platform: str,
    space_id: str,
    channel_id: str,
    anchor_message_id: str,
    window_start: str,
    window_end: str,
    score: float,
    signals_json: str,
    reason: str | None,
    kind: str = "opportunity",
    cooldown_minutes: int = 45,
    expires_at: str | None = None,
    now: str | None = None,
) -> int | None:
    """Write a flag unless the per-(platform, channel_id, kind) cooldown suppresses it.

    Returns the new flag id, or None if a non-terminal flag of the same kind already sits in
    the cooldown window (the dedupe path — not an error). Caller MUST be inside
    ``immediate_txn`` so the check-then-insert is atomic against a concurrent watcher tick."""
    now = now or _utc_now_iso()
    if find_active_flag_in_window(
        conn,
        platform=platform,
        channel_id=channel_id,
        kind=kind,
        cooldown_minutes=cooldown_minutes,
        now=now,
    ) is not None:
        return None

    row = conn.execute(
        _sa_text(
            "INSERT INTO community_conversation_flags "
            "  (org_id, platform, space_id, channel_id, anchor_message_id, window_start, "
            "   window_end, score, kind, signals_json, reason, status, expires_at, created_at) "
            "VALUES (:org, :p, :sp, :ch, :anchor, :ws, :we, :score, :kind, :sig, :reason, "
            "   'active', :exp, :now) "
            "RETURNING id"
        ),
        {
            "org": org_id,
            "p": platform,
            "sp": str(space_id),
            "ch": str(channel_id),
            "anchor": str(anchor_message_id),
            "ws": window_start,
            "we": window_end,
            "score": float(score),
            "kind": kind,
            "sig": signals_json,
            "reason": reason,
            "exp": expires_at,
            "now": now,
        },
    ).fetchone()
    return int(row[0]) if row is not None else None


def list_active_flags(
    conn: Connection,
    org_id: str,
    *,
    status: str | None = "active",
    limit: int = 50,
) -> list[dict]:
    """Flags for one org, newest first, filtered to ``status`` (default 'active' — the
    not-yet-delivered queue). Pass ``status=None`` for all statuses. Read-only."""
    q = (
        f"SELECT {_COLS} FROM community_conversation_flags "
        "WHERE org_id = :org "
    )
    params: dict = {"org": org_id, "lim": int(limit)}
    if status is not None:
        q += "AND status = :st "
        params["st"] = status
    q += "ORDER BY created_at DESC LIMIT :lim"
    rows = conn.execute(_sa_text(q), params).fetchall()
    return [dict(r._mapping) for r in rows]


def list_deliverable_flags(
    conn: Connection, *, limit: int = 100
) -> list[dict]:
    """Active flags across ALL orgs awaiting delivery — the deliverer's work queue. Ordered
    brand_risk-first (kind DESC puts 'opportunity' before 'brand_risk' alphabetically, so we
    invert with a CASE), then oldest-first so nothing starves. Read-only."""
    rows = conn.execute(
        _sa_text(
            f"SELECT {_COLS} FROM community_conversation_flags "
            "WHERE status = 'active' "
            "ORDER BY CASE kind WHEN 'brand_risk' THEN 0 ELSE 1 END, created_at ASC "
            "LIMIT :lim"
        ),
        {"lim": int(limit)},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def mark_delivered(
    conn: Connection, flag_id: int, *, now: str | None = None
) -> bool:
    """Flip 'active' -> 'delivered' after the flag is posted to the client's TG topic. Single-
    flight: only an 'active' row transitions, so a redelivery race never double-posts. Returns
    True iff this call claimed it. Caller in txn."""
    now = now or _utc_now_iso()
    result = conn.execute(
        _sa_text(
            "UPDATE community_conversation_flags "
            "SET status = 'delivered', delivered_at = :now "
            "WHERE id = :id AND status = 'active'"
        ),
        {"id": int(flag_id), "now": now},
    )
    return (result.rowcount or 0) > 0


def record_feedback(
    conn: Connection, flag_id: int, *, verdict: str
) -> bool:
    """Record the operator verdict from the flag's inline buttons and terminate the flag:
    'pitched' -> status 'handled', 'noise' -> status 'noise'. This is the precision-gate
    signal calibration reads. Returns True iff the flag existed and was updated. Caller in txn."""
    if verdict not in ("pitched", "noise"):
        raise ValueError(f"verdict must be 'pitched' or 'noise', got {verdict!r}")
    new_status = "handled" if verdict == "pitched" else "noise"
    result = conn.execute(
        _sa_text(
            "UPDATE community_conversation_flags "
            "SET feedback = :v, status = :st "
            "WHERE id = :id AND status IN ('active', 'delivered')"
        ),
        {"id": int(flag_id), "v": verdict, "st": new_status},
    )
    return (result.rowcount or 0) > 0


def gc_expired_flags(
    conn: Connection, *, now: str | None = None
) -> int:
    """Expire active/delivered flags past their ``expires_at`` (status -> 'expired') so a stale
    conversation stops occupying the feed and its cooldown lapses. Returns rows expired.
    Feedback is preserved (calibration reads terminal rows too). Caller in txn."""
    now = now or _utc_now_iso()
    result = conn.execute(
        _sa_text(
            "UPDATE community_conversation_flags SET status = 'expired' "
            "WHERE status IN ('active', 'delivered') "
            "  AND expires_at IS NOT NULL AND expires_at <= :now"
        ),
        {"now": now},
    )
    return result.rowcount or 0
