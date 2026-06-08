"""Media recommendation-center CRUD (migration 066).

Backs Slopper's no-LLM media-recommendation ranker (the per-reply Claude clip-rank
replacement). Three tables, all owned by SablePlatform and reached in-process by
Slopper / SableWeb (never by writing ``sable.db`` directly):

  * ``media_rec_events`` — one row per media "slate" we offered an operator for a
    reply: the ordered ``slate_json`` of offered ``content_id``s + the
    ``chosen_content_id`` (NULL if no media was attached) + an ``applied`` flag.
    This is the SOURCE OF TRUTH for the learned quality.
  * ``media_quality`` — the materialized per-asset Elo rollup, derived FORWARD-ONLY
    from the choice log by :func:`apply_pending_media_events` (chosen beats every
    other offered asset in the same slate, pairwise). ``media_quality`` is a cache
    of the events, never the truth.
  * ``media_embeddings`` — a per-(org, asset) semantic-embedding cache
    (``embedding_json`` + the producing ``embedding_model``) for similarity recall.
    A model swap is detected by the caller comparing ``embedding_model``.

There is NO cost column anywhere here — cost lives only in ``cost_events`` (the
066 migration header makes this explicit). Reads are transaction-free; writers
require an ``immediate_txn`` (the C2.2 contract — `apply_pending_media_events`
opens one internally). Caller owns the connection lifecycle.

CompatConnection gotcha (feedback_compatconn_row_access): iterating/unpacking a
Row yields column NAMES, indexing yields values — every read here maps by
positional index against an explicit column tuple.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

# Elo K-factor for the incremental chosen-beats-the-rest pairwise update. A modest
# K keeps a single slate from swinging an asset's rating wildly — the signal is
# the AGGREGATE of many slates, not any one pick.
_ELO_K = 16.0
_ELO_BASE = 1500.0
# Elo logistic scale (the standard 400). Exposed so the ranker's normalization
# ((elo-1500)/400) and this update agree on the denominator.
_ELO_SCALE = 400.0


# ------------------------------------------------------------------
# Quality rollup (forward-only Elo from the choice log)
# ------------------------------------------------------------------
def get_media_quality(conn: Connection, org_id: str) -> dict[str, dict[str, float]]:
    """Return the materialized per-asset quality rollup for ``org_id``.

    ``{content_id: {"elo": float, "pick_rate": float}}`` where
    ``pick_rate = n_chosen / max(n_offered, 1)`` — empty when the org has no
    recorded slates yet (a cold-start library). Read-only, org-scoped. The ranker
    normalizes ``elo`` + ``pick_rate`` into the quality prior; an asset absent
    from the map is treated as the Elo base with zero exposure.
    """
    rows = conn.execute(
        text(
            "SELECT content_id, elo, n_offered, n_chosen "
            "FROM media_quality WHERE org_id = :org"
        ),
        {"org": org_id},
    ).fetchall()
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        n_offered = int(r[2] or 0)
        n_chosen = int(r[3] or 0)
        out[str(r[0])] = {
            "elo": float(r[1]) if r[1] is not None else _ELO_BASE,
            "pick_rate": n_chosen / max(n_offered, 1),
        }
    return out


def _expected(elo_a: float, elo_b: float) -> float:
    """Standard Elo expected score of A vs B on the logistic curve."""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / _ELO_SCALE))


def apply_pending_media_events(conn: Connection, org_id: str) -> int:
    """Catch the Elo rollup up to any unapplied slates for ``org_id``.

    Scans ``media_rec_events`` for this org where ``applied = 0`` (the
    ``ix_media_rec_events_unapplied`` index covers it), and for each slate updates
    ``media_quality``: every OFFERED asset gets ``n_offered += 1``; the
    ``chosen_content_id`` (when present and in the slate) gets ``n_chosen += 1``
    and a pairwise Elo bump against EVERY other offered asset (chosen scored 1,
    the losers 0), so a consistently-picked asset drifts above the 1500 base.
    Each processed event is then marked ``applied = 1`` so the next call is
    incremental (forward-only — events are never re-applied).

    Idempotent + crash-safe: the whole catch-up runs inside ONE ``immediate_txn``,
    so a crash mid-way rolls back BOTH the quality bumps and the applied flags
    together (the slate re-applies cleanly next time). Returns the number of
    events applied (0 when already caught up). Best-effort by contract: any
    error inside is allowed to propagate to the immediate_txn rollback; the
    Slopper caller wraps this in its own try/except so a rollup hiccup never
    breaks a reply.
    """
    from sable_platform.relay.bot.txn import immediate_txn

    # The connection Slopper hands us is a CompatConnection wrapping a SA
    # Connection; immediate_txn drives the raw SA Connection.
    sa_conn = getattr(conn, "_conn", conn)

    applied = 0
    with immediate_txn(sa_conn):
        rows = conn.execute(
            text(
                "SELECT id, slate_json, chosen_content_id "
                "FROM media_rec_events "
                "WHERE org_id = :org AND applied = 0 "
                "ORDER BY id ASC"
            ),
            {"org": org_id},
        ).fetchall()

        for r in rows:
            event_id = r[0]
            slate = _parse_slate(r[1])
            chosen = r[2]
            if slate:
                _apply_one_slate(conn, org_id, slate, chosen)
            conn.execute(
                text("UPDATE media_rec_events SET applied = 1 WHERE id = :id"),
                {"id": event_id},
            )
            applied += 1

    return applied


def _parse_slate(slate_json: str | None) -> list[str]:
    try:
        data = json.loads(slate_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(c) for c in data] if isinstance(data, list) else []


def _apply_one_slate(
    conn: Connection, org_id: str, slate: list[str], chosen: str | None
) -> None:
    """Apply one slate's outcome to media_quality (within the caller's txn)."""
    # Materialize every offered asset to its current Elo (defaulting to base).
    elos = {cid: _current_elo(conn, org_id, cid) for cid in slate}

    # n_offered += 1 for every offered asset.
    for cid in slate:
        _bump_quality(conn, org_id, cid, d_offered=1, d_chosen=0, new_elo=elos[cid])

    if chosen and chosen in elos:
        # Pairwise: chosen (score 1) vs each other offered asset (score 0). The
        # rating deltas are accumulated against the SNAPSHOT elos so the order of
        # opponents within the slate doesn't matter.
        delta_chosen = 0.0
        for cid in slate:
            if cid == chosen:
                continue
            exp_chosen = _expected(elos[chosen], elos[cid])
            delta_chosen += _ELO_K * (1.0 - exp_chosen)
            # The loser's symmetric loss.
            exp_loser = _expected(elos[cid], elos[chosen])
            loser_elo = elos[cid] + _ELO_K * (0.0 - exp_loser)
            _bump_quality(conn, org_id, cid, d_offered=0, d_chosen=0, new_elo=loser_elo)
        winner_elo = elos[chosen] + delta_chosen
        _bump_quality(
            conn, org_id, chosen, d_offered=0, d_chosen=1, new_elo=winner_elo
        )


def _current_elo(conn: Connection, org_id: str, content_id: str) -> float:
    row = conn.execute(
        text(
            "SELECT elo FROM media_quality WHERE org_id = :org AND content_id = :cid"
        ),
        {"org": org_id, "cid": content_id},
    ).fetchone()
    if row is None or row[0] is None:
        return _ELO_BASE
    return float(row[0])


def _bump_quality(
    conn: Connection,
    org_id: str,
    content_id: str,
    *,
    d_offered: int,
    d_chosen: int,
    new_elo: float,
) -> None:
    """Upsert a media_quality row: set elo + increment the counters.

    Uses a read-then-write upsert (dialect-agnostic — no ON CONFLICT) since the
    whole catch-up is already serialized inside the caller's immediate_txn.
    """
    exists = conn.execute(
        text(
            "SELECT 1 FROM media_quality WHERE org_id = :org AND content_id = :cid"
        ),
        {"org": org_id, "cid": content_id},
    ).fetchone()
    if exists is None:
        conn.execute(
            text(
                "INSERT INTO media_quality (org_id, content_id, elo, n_offered, n_chosen) "
                "VALUES (:org, :cid, :elo, :off, :cho)"
            ),
            {
                "org": org_id,
                "cid": content_id,
                "elo": float(new_elo),
                "off": int(d_offered),
                "cho": int(d_chosen),
            },
        )
    else:
        conn.execute(
            text(
                "UPDATE media_quality SET elo = :elo, "
                "n_offered = n_offered + :off, n_chosen = n_chosen + :cho "
                "WHERE org_id = :org AND content_id = :cid"
            ),
            {
                "org": org_id,
                "cid": content_id,
                "elo": float(new_elo),
                "off": int(d_offered),
                "cho": int(d_chosen),
            },
        )


def log_media_rec_event(
    conn: Connection,
    org_id: str,
    operator_handle: str | None,
    tweet_ref: str | None,
    slate: list[str],
    chosen_content_id: str | None,
) -> int:
    """Append one offered slate to ``media_rec_events`` (the choice log).

    ``slate`` is the ordered list of offered ``content_id``s; ``chosen_content_id``
    is the one the operator attached (or None — offered a slate, attached nothing).
    Returns the new event id. The row lands ``applied = 0`` so the next
    :func:`apply_pending_media_events` folds it into the Elo. Caller commits (or
    wraps in its own txn). This is the WRITE side used when an operator picks; the
    ranker itself only READS quality.
    """
    res = conn.execute(
        text(
            "INSERT INTO media_rec_events "
            "(org_id, operator_handle, tweet_ref, slate_json, chosen_content_id, applied) "
            "VALUES (:org, :op, :tref, :slate, :chosen, 0) "
            "RETURNING id"
        ),
        {
            "org": org_id,
            "op": operator_handle,
            "tref": tweet_ref,
            "slate": json.dumps([str(c) for c in slate]),
            "chosen": chosen_content_id,
        },
    ).fetchone()
    return int(res[0])


# ------------------------------------------------------------------
# Per-asset embedding cache
# ------------------------------------------------------------------
def get_media_embedding(
    conn: Connection, org_id: str, content_id: str
) -> tuple[list[float], str] | None:
    """Return the cached ``(vector, model)`` for an asset, or None.

    ``None`` means the asset is not cached for this org (or its ``embedding_json``
    is NULL) — the caller must embed it. When a row IS returned, the vector is
    decoded from ``embedding_json`` into ``list[float]`` and ``embedding_model`` is
    the producing provider/model (``""`` on a legacy row with no model). The caller
    compares ``embedding_model`` against the active model and re-embeds on a swap.
    Read-only, org+asset scoped.
    """
    row = conn.execute(
        text(
            "SELECT embedding_json, embedding_model FROM media_embeddings "
            "WHERE org_id = :org AND content_id = :cid"
        ),
        {"org": org_id, "cid": content_id},
    ).fetchone()
    if row is None or row[0] is None:
        return None
    try:
        vector = [float(x) for x in json.loads(row[0])]
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return (vector, str(row[1]) if row[1] is not None else "")


def set_media_embedding(
    conn: Connection,
    org_id: str,
    content_id: str,
    vector: list[float],
    model: str,
) -> None:
    """Upsert the cached embedding vector + producing model for an asset.

    JSON-encodes ``vector`` (a ``list[float]``) into ``embedding_json``. Keyed by
    ``(org_id, content_id)`` — the same content under two orgs is two rows (066
    header). Dialect-agnostic read-then-write upsert (a model swap overwrites in
    place, detected on read via ``embedding_model``). The caller MUST be inside an
    ``immediate_txn`` (this writes); caller commits.
    """
    embedding_json = json.dumps([float(x) for x in vector])
    exists = conn.execute(
        text(
            "SELECT 1 FROM media_embeddings WHERE org_id = :org AND content_id = :cid"
        ),
        {"org": org_id, "cid": content_id},
    ).fetchone()
    if exists is None:
        conn.execute(
            text(
                "INSERT INTO media_embeddings "
                "(org_id, content_id, embedding_json, embedding_model) "
                "VALUES (:org, :cid, :ej, :em)"
            ),
            {"org": org_id, "cid": content_id, "ej": embedding_json, "em": model},
        )
    else:
        conn.execute(
            text(
                "UPDATE media_embeddings SET embedding_json = :ej, embedding_model = :em "
                "WHERE org_id = :org AND content_id = :cid"
            ),
            {"org": org_id, "cid": content_id, "ej": embedding_json, "em": model},
        )


# ------------------------------------------------------------------
# Reply-outcome media stamp
# ------------------------------------------------------------------
def stamp_outcome_media(
    conn: Connection,
    suggestion_id: str,
    media_content_id: str | None,
) -> bool:
    """Record which media asset rode along with a posted reply.

    Sets ``reply_outcomes.media_content_id`` for the outcome(s) keyed by
    ``suggestion_id``. ``reply_outcomes`` has its own surrogate ``id`` PK, but the
    application keys a posted reply by its originating ``suggestion_id`` — that is
    how :func:`sable_platform.db.replies.record_outcome` writes/matches an outcome
    (on ``(suggestion_id, posted_tweet_id)``), so the media stamp follows the same
    key. Returns ``True`` iff a row was updated. The caller MUST be inside a write
    transaction; caller commits.
    """
    result = conn.execute(
        text(
            "UPDATE reply_outcomes SET media_content_id = :mid "
            "WHERE suggestion_id = :sid"
        ),
        {"mid": media_content_id, "sid": suggestion_id},
    )
    return int(result.rowcount or 0) > 0
