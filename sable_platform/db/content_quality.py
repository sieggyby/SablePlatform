"""Content-preference Elo rollup for the Content Deck A/B duel (mig 080) — PARALLEL to ``db/media.py``.

The deck's pairwise duel (SableWeb ``/api/ops/content-deck/duel``) records a row to
``content_deck_decisions`` (winner = ``candidate_id``, loser = ``pair_loser_id``, ``decision='keep'``)
with NO status flip — a pure per-operator preference signal. ``apply_pending_content_events`` folds the
unapplied duel rows forward-only into ``content_quality`` at TWO grains:

  * ``subject_kind='candidate'`` — a per-candidate Elo, a LIVE within-deck tie-break overlay only.
    Candidates are ephemeral (soft-expire/GC), so this never converges and dies with the candidate;
    it is rendered behind a caveat, never a verdict.
  * ``subject_kind='feature'`` — a LIKE-TO-LIKE feature Elo (``kind:<kind>`` / ``template:<id>`` /
    ``format:<fmt>`` from the candidate payload). This accumulates ACROSS candidates and survives GC —
    it is the DURABLE signal the Tweet-Quality engine consumes (gated on a min-sample floor there).

Mirrors the media Elo (base 1500, K=16, logistic scale 400; offered/chosen counters → pick=win rate;
forward-only ``applied`` flag; whole catch-up in ONE ``immediate_txn`` so a crash rolls back the bumps
AND the flags together). NO cost column, ever.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

_ELO_K = 16.0
_ELO_BASE = 1500.0
_ELO_SCALE = 400.0  # standard logistic scale; matches db/media.py so the two rollups agree

# The candidate features a duel folds at FEATURE grain — compared LIKE-TO-LIKE (kind vs kind,
# template vs template, format vs format), never across types.
_FEATURE_TYPES = ("kind", "template", "format")


def _now_iso() -> str:
    """Second-precision UTC-Z, matching isoZ() / the SQLite DEFAULT. Provided EXPLICITLY by the app on
    every write so the dialect-specific ``strftime`` DEFAULT (SQLite-only) is never relied on (Postgres
    has no strftime)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expected(elo_a: float, elo_b: float) -> float:
    """Standard Elo expected score of A vs B on the logistic curve."""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / _ELO_SCALE))


def get_content_quality(
    conn: Connection, org_id: str, subject_kind: str | None = None
) -> dict[str, dict[str, float]]:
    """Materialized content-quality rollup for ``org_id``: ``{subject_key: {elo, pick_rate, n_offered,
    n_chosen}}``. ``subject_kind`` filters 'candidate' (live tie-break) vs 'feature' (durable engine
    signal); None returns all (callers pass a specific kind so the two never mix). Read-only, org-
    scoped, NO cost column. Degrades to {} when the table is absent (below mig 080) — caller-wrapped."""
    sql = (
        "SELECT subject_key, elo, n_offered, n_chosen FROM content_quality WHERE org_id = :org"
    )
    params: dict = {"org": org_id}
    if subject_kind is not None:
        sql += " AND subject_kind = :sk"
        params["sk"] = subject_kind
    rows = conn.execute(text(sql), params).fetchall()
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        n_offered = int(r[2] or 0)
        n_chosen = int(r[3] or 0)
        out[str(r[0])] = {
            "elo": float(r[1]) if r[1] is not None else _ELO_BASE,
            "pick_rate": n_chosen / max(n_offered, 1),
            "n_offered": float(n_offered),
            "n_chosen": float(n_chosen),
        }
    return out


def apply_pending_content_events(conn: Connection, org_id: str) -> int:
    """Catch the content Elo up to any unapplied DUEL rows for ``org_id`` (forward-only, idempotent).

    Scans every unapplied ``content_deck_decisions`` row for ``org_id`` (``applied = 0``, covered by
    ``ix_content_deck_decisions_unapplied``) and marks each ``applied = 1`` — but only a DUEL row (one
    carrying a ``pair_loser_id``) folds into the Elo; a swipe row (NULL ``pair_loser_id``) is cursored
    forward without folding (its keep-rate signal is read directly by the engine, NOT via this flag, so
    marking it keeps the unapplied set bounded). Each duel applies a candidate-grain pairwise Elo
    (winner = ``candidate_id`` beats loser = ``pair_loser_id``) plus a like-to-like FEATURE-grain
    update. The whole catch-up runs inside ONE ``immediate_txn`` so a crash rolls back BOTH the quality
    bumps and the applied flags together (forward-only, idempotent). Returns the number of DUELS folded
    (0 when caught up). Best-effort by contract: the caller wraps this so a rollup hiccup never breaks a
    deck read."""
    from sable_platform.relay.bot.txn import immediate_txn

    sa_conn = getattr(conn, "_conn", conn)
    now = _now_iso()
    folded = 0
    with immediate_txn(sa_conn):
        rows = conn.execute(
            text(
                "SELECT id, candidate_id, pair_loser_id, decision, actor_kind "
                "FROM content_deck_decisions "
                "WHERE org_id = :org AND applied = 0 ORDER BY id ASC"
            ),
            {"org": org_id},
        ).fetchall()
        for r in rows:
            event_id, winner_id, loser_id, decision, actor_kind = r[0], r[1], r[2], r[3], r[4]
            # Fold ONLY a genuine duel: a pair_loser_id carried on a 'keep' row (the winner). The
            # decision guard future-proofs the Elo against a stray pair_loser_id ever appearing on a
            # reject/skip row (which would fold with inverted semantics) — the /duel route only ever
            # writes decision='keep' + pair_loser_id, so today this is purely defensive.
            if (
                loser_id is not None
                and winner_id is not None
                and decision == "keep"
                and int(winner_id) != int(loser_id)
            ):
                # Phase-5 QUARANTINE: a COMMUNITY duel (the Discord game) folds into
                # 'community:'-prefixed subject keys at BOTH grains — structurally separate
                # rows in the same table, so community taste NEVER pollutes the operator
                # Elo that steers ranking/production. Every operator-Elo consumer keys on
                # unprefixed 'kind:/template:/format:' (or bare candidate ids) and can't
                # match a prefixed row; promotion past the quarantine is gated on the
                # masterplan §11 K-tests (a deliberate future change, not a config flip).
                prefix = "community:" if actor_kind == "community" else ""
                _apply_one_duel(conn, org_id, int(winner_id), int(loser_id), now, key_prefix=prefix)
                folded += 1
            conn.execute(
                text("UPDATE content_deck_decisions SET applied = 1 WHERE id = :id"),
                {"id": event_id},
            )
    return folded


def _apply_one_duel(conn: Connection, org_id: str, winner_id: int, loser_id: int, now: str,
                    *, key_prefix: str = "") -> None:
    """Fold one duel (winner beat loser) at candidate grain (live tie-break) + feature grain (durable).
    ``key_prefix`` namespaces BOTH grains' subject keys ('community:' for the Phase-5 Discord game —
    the quarantine that keeps community taste out of the operator Elo)."""
    # candidate grain — the live within-deck tie-break overlay (ephemeral; GC's with the candidate).
    _pairwise(conn, org_id, "candidate", f"{key_prefix}{winner_id}", f"{key_prefix}{loser_id}", now)
    # feature grain — like-to-like over the candidates' kind/template/format (durable engine signal).
    wfeat = _feature_map(conn, org_id, winner_id)
    lfeat = _feature_map(conn, org_id, loser_id)
    # ``template``/``format`` are KIND-SPECIFIC vocabularies — a meme template id and a tweet format
    # bucket are not comparable, and the deck duels ACROSS kinds (getDeckDuelPair pairs any two
    # pending cards, no kind filter). So those two arms fold ONLY when the pair shares a kind; a
    # cross-kind duel contributes the ``kind`` arm alone (its meaningful signal). ``kind`` always
    # folds. (Before text candidates stamped a ``format``, this was moot — only one side ever carried
    # one; it becomes load-bearing now that tweets/threads populate ``format:`` too.)
    # NOTE: the feature subject_key (``format:<value>``) is not itself kind-namespaced, so this gate
    # guards DUEL pairing, not subject_key pooling. The producers keep the two ``format`` vocabularies
    # disjoint at the source — the text producer stamps ``text:<bucket>`` while the meme producer
    # stamps the template display name — so a text-format row and a meme-format row can never share a
    # subject_key even though they live in one namespace.
    same_kind = wfeat.get("kind") is not None and wfeat.get("kind") == lfeat.get("kind")
    for t in _FEATURE_TYPES:
        if t != "kind" and not same_kind:
            continue
        wv, lv = wfeat.get(t), lfeat.get(t)
        if wv and lv and wv != lv:
            _pairwise(conn, org_id, "feature", f"{key_prefix}{t}:{wv}", f"{key_prefix}{t}:{lv}", now)


def _feature_map(conn: Connection, org_id: str, candidate_id: int) -> dict[str, str]:
    """The duel-foldable feature values for a candidate (kind always; template/format from the meme
    payload). Returns {} when the candidate is GC'd (the NO-FK decisions log survives a candidate purge)
    — that side simply contributes no feature signal."""
    row = conn.execute(
        text("SELECT kind, payload_json FROM content_candidates WHERE id = :id AND org_id = :org"),
        {"id": candidate_id, "org": org_id},
    ).fetchone()
    if row is None:
        return {}
    feats: dict[str, str] = {}
    if row[0]:
        feats["kind"] = str(row[0])
    try:
        p = json.loads(row[1] or "{}")
        if isinstance(p, dict):
            tid = p.get("template_id")
            if isinstance(tid, str) and tid:
                feats["template"] = tid
            fmt = p.get("format")
            if isinstance(fmt, str) and fmt:
                feats["format"] = fmt
    except (TypeError, ValueError):
        pass
    return feats


def _pairwise(conn: Connection, org_id: str, subject_kind: str, winner_key: str, loser_key: str, now: str) -> None:
    """One pairwise Elo update (winner_key beat loser_key) at the given grain. Both subjects get
    n_offered += 1; the winner gets n_chosen += 1 (so pick_rate == win rate)."""
    we = _current_elo(conn, org_id, subject_kind, winner_key)
    le = _current_elo(conn, org_id, subject_kind, loser_key)
    _bump_quality(conn, org_id, subject_kind, winner_key, d_offered=1, d_chosen=1,
                  new_elo=we + _ELO_K * (1.0 - _expected(we, le)), now=now)
    _bump_quality(conn, org_id, subject_kind, loser_key, d_offered=1, d_chosen=0,
                  new_elo=le + _ELO_K * (0.0 - _expected(le, we)), now=now)


def _current_elo(conn: Connection, org_id: str, subject_kind: str, subject_key: str) -> float:
    row = conn.execute(
        text(
            "SELECT elo FROM content_quality "
            "WHERE org_id = :org AND subject_kind = :sk AND subject_key = :key"
        ),
        {"org": org_id, "sk": subject_kind, "key": subject_key},
    ).fetchone()
    if row is None or row[0] is None:
        return _ELO_BASE
    return float(row[0])


def _bump_quality(
    conn: Connection,
    org_id: str,
    subject_kind: str,
    subject_key: str,
    *,
    d_offered: int,
    d_chosen: int,
    new_elo: float,
    now: str,
) -> None:
    """Upsert a content_quality row: set elo + increment the counters + stamp updated_at. Read-then-
    write (dialect-agnostic, no ON CONFLICT) since the catch-up is already serialized in the caller's
    immediate_txn. ``updated_at`` is passed explicitly (never the SQLite-only strftime DEFAULT)."""
    exists = conn.execute(
        text(
            "SELECT 1 FROM content_quality "
            "WHERE org_id = :org AND subject_kind = :sk AND subject_key = :key"
        ),
        {"org": org_id, "sk": subject_kind, "key": subject_key},
    ).fetchone()
    if exists is None:
        conn.execute(
            text(
                "INSERT INTO content_quality "
                "(org_id, subject_kind, subject_key, elo, n_offered, n_chosen, updated_at) "
                "VALUES (:org, :sk, :key, :elo, :off, :cho, :now)"
            ),
            {"org": org_id, "sk": subject_kind, "key": subject_key,
             "elo": float(new_elo), "off": int(d_offered), "cho": int(d_chosen), "now": now},
        )
    else:
        conn.execute(
            text(
                "UPDATE content_quality SET elo = :elo, "
                "n_offered = n_offered + :off, n_chosen = n_chosen + :cho, updated_at = :now "
                "WHERE org_id = :org AND subject_kind = :sk AND subject_key = :key"
            ),
            {"org": org_id, "sk": subject_kind, "key": subject_key,
             "elo": float(new_elo), "off": int(d_offered), "cho": int(d_chosen), "now": now},
        )
