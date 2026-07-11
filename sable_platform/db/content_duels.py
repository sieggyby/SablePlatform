"""Durable open-duel registry (mig 084) — makes a 24h community duel survive a bot restart.

discord.py Views are in-memory: on restart the vote buttons die and the auto-reveal never
fires. This table is the source of truth the bot rebinds from — a persistent view routes
any button click to a lookup by ``message_id``, and a background sweep closes any row whose
``deadline`` has passed (including a startup pass for duels that expired while the bot was
down). One OPEN row per channel is the restart-safe "a duel is live here" lock.

``card_a_json`` / ``card_b_json`` are RENDERED-CARD SNAPSHOTS captured at post time, so the
close reveal never depends on the ``content_candidates`` rows still existing. The VOTES stay
in ``content_deck_decisions`` (``count_duel_votes`` tallies them since ``opened_at``); this
module only owns the duel's identity + snapshot + lifecycle. Writers need the caller's
``immediate_txn``; reads are transaction-free. No cost column.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text as _sa_text
from sqlalchemy.engine import Connection

_COLS = "message_id, org_id, guild_id, channel_id, card_a_json, card_b_json, opened_at, deadline, status, closed_at"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def open_duel(
    conn: Connection,
    *,
    message_id: str,
    org_id: str,
    guild_id: str,
    channel_id: str,
    card_a_json: str,
    card_b_json: str,
    opened_at: str,
    deadline: str,
) -> None:
    """Record a freshly-posted duel (status='open'). Caller in ``immediate_txn``."""
    conn.execute(
        _sa_text(
            "INSERT INTO content_duels "
            " (message_id, org_id, guild_id, channel_id, card_a_json, card_b_json, opened_at, deadline) "
            "VALUES (:m, :org, :g, :ch, :a, :b, :o, :d)"
        ),
        {"m": message_id, "org": org_id, "g": guild_id, "ch": channel_id,
         "a": card_a_json, "b": card_b_json, "o": opened_at, "d": deadline},
    )


def get_duel(conn: Connection, message_id: str) -> dict | None:
    """The duel row by Discord message id, or None. Read-only."""
    row = conn.execute(
        _sa_text(f"SELECT {_COLS} FROM content_duels WHERE message_id = :m"),
        {"m": str(message_id)},
    ).fetchone()
    return dict(row._mapping) if row is not None else None


def channel_has_open_duel(conn: Connection, channel_id: str) -> bool:
    """Whether an OPEN duel exists in this channel — the DURABLE per-channel lock (survives
    restart, unlike the bot's in-memory reservation). A past-deadline-but-unclosed row still
    counts (its message still shows live buttons) — the sweep closes it within its cadence."""
    row = conn.execute(
        _sa_text("SELECT 1 FROM content_duels WHERE channel_id = :ch AND status = 'open' LIMIT 1"),
        {"ch": str(channel_id)},
    ).fetchone()
    return row is not None


def list_due_duels(conn: Connection, *, now: str | None = None, limit: int = 100) -> list[dict]:
    """OPEN duels whose ``deadline`` has passed — the close-sweep targets (includes any that
    expired while the bot was down, since it's a plain deadline<=now query). Read-only."""
    now = now or _utc_now_iso()
    rows = conn.execute(
        _sa_text(
            f"SELECT {_COLS} FROM content_duels "
            "WHERE status = 'open' AND deadline <= :now ORDER BY deadline ASC LIMIT :lim"
        ),
        {"now": now, "lim": limit},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def close_duel(conn: Connection, message_id: str, *, now: str | None = None) -> bool:
    """Single-flight close: flip status open->closed ONLY if currently open (so two concurrent
    sweeps never double-reveal). Returns True iff this call claimed the close. Caller in txn."""
    now = now or _utc_now_iso()
    result = conn.execute(
        _sa_text(
            "UPDATE content_duels SET status = 'closed', closed_at = :now "
            "WHERE message_id = :m AND status = 'open'"
        ),
        {"m": str(message_id), "now": now},
    )
    return (result.rowcount or 0) > 0


def count_duel_votes(
    conn: Connection, org_id: str, card_a_id: int, card_b_id: int, since: str
) -> tuple[int, int]:
    """(votes_for_A, votes_for_B) for THIS duel — counted from ``content_deck_decisions``
    (the durable vote ledger) since ``opened_at``, which bounds it to this duel (the same
    pair can't re-duel within the reuse window). A vote for A is a keep row winner=A/loser=B
    and vice-versa. Read-only."""
    rows = conn.execute(
        _sa_text(
            "SELECT candidate_id, COUNT(*) AS n FROM content_deck_decisions "
            "WHERE org_id = :org AND surface = 'discord' AND actor_kind = 'community' "
            "  AND decision = 'keep' AND pair_loser_id IS NOT NULL AND created_at >= :since "
            "  AND ((candidate_id = :a AND pair_loser_id = :b) "
            "    OR (candidate_id = :b AND pair_loser_id = :a)) "
            "GROUP BY candidate_id"
        ),
        {"org": org_id, "since": since, "a": int(card_a_id), "b": int(card_b_id)},
    ).fetchall()
    counts = {int(r._mapping["candidate_id"]): int(r._mapping["n"]) for r in rows}
    return counts.get(int(card_a_id), 0), counts.get(int(card_b_id), 0)
