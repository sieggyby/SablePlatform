"""Content Deck candidate-substrate CRUD (migration 076).

The durable home for the ambient generate->swipe->schedule loop -- see
~/sable-workspace/CONTENT_DECK_MASTERPLAN.md. Three tables:

  content_candidates          -- the ambient queue (one row per generated candidate)
  content_deck_decisions      -- the swipe log (keep/reject/skip + pairwise duels)
  content_deck_operator_state -- per-operator dismiss/snooze

``org_id`` is the scope wall on every accessor. NO cost column or selection, ever. Writers
require an ``immediate_txn`` (the caller commits); reads are transaction-free. These are
org-scoped DATA access -- per-account publish authorization (target_handle) lives in the
caller (SableWeb composeAccountsFor/composePersonasFor), not here.

LOAD-BEARING SAFETY (from the audit):
  * ``get_candidate_org`` is FAIL-CLOSED: the candidate id is the auth object, so
    ``record_deck_decision`` / ``set_candidate_status`` reject a candidate that does not
    resolve to the claimed org (NOT the reply path's fail-open join semantics).
  * ``pair_loser_id`` (a second candidate ref) must resolve to the SAME org as
    ``candidate_id`` -- a cross-org loser would poison the org-scoped Elo/BT (Codex r1).
  * ``expire_due_candidates`` only touches ``status='pending'`` -- a kept/scheduled
    candidate is never auto-expired out from under a future ``publish_at`` (round-3).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text as _sa_text
from sqlalchemy.engine import Connection

from sable_platform.db.compat import get_dialect, json_extract_text

_CAND_COLS = (
    "id, org_id, kind, status, target_handle, payload_json, media_content_id, source, "
    "score, score_reason, tell_score, dedupe_key, expires_at, created_at"
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# content_candidates
# ---------------------------------------------------------------------------
def upsert_candidate(
    conn: Connection,
    *,
    org_id: str,
    kind: str,
    payload_json: str,
    source: str,
    target_handle: str | None = None,
    media_content_id: str | None = None,
    score: float | None = None,
    score_reason: str | None = None,
    tell_score: float | None = None,
    dedupe_key: str | None = None,
    expires_at: str | None = None,
    now: str | None = None,
) -> int:
    """Insert a candidate and return its id. App-level dedup: if ``dedupe_key`` is given and
    a PENDING candidate already exists for ``(org_id, dedupe_key)``, return that id without
    inserting (exact-content re-emit -- the dedupe_key only catches identical re-emits, the
    real flood control is the producer's cap-N). The caller MUST be in an immediate_txn.
    """
    if dedupe_key:
        existing = conn.execute(
            _sa_text(
                "SELECT id FROM content_candidates "
                "WHERE org_id = :org AND dedupe_key = :dk AND status = 'pending' "
                "ORDER BY id LIMIT 1"
            ),
            {"org": org_id, "dk": dedupe_key},
        ).fetchone()
        if existing is not None:
            return int(existing[0])

    row = conn.execute(
        _sa_text(
            "INSERT INTO content_candidates "
            "  (org_id, kind, status, target_handle, payload_json, media_content_id, source, "
            "   score, score_reason, tell_score, dedupe_key, expires_at, created_at) "
            "VALUES (:org, :kind, 'pending', :handle, :payload, :media, :source, "
            "   :score, :reason, :tell, :dk, :expires, :now) "
            "RETURNING id"
        ),
        {
            "org": org_id,
            "kind": kind,
            "handle": target_handle,
            "payload": payload_json,
            "media": media_content_id,
            "source": source,
            "score": score,
            "reason": score_reason,
            "tell": tell_score,
            "dk": dedupe_key,
            "expires": expires_at,
            "now": now or _utc_now_iso(),
        },
    ).fetchone()
    return int(row[0]) if row is not None else 0


def get_candidate(conn: Connection, candidate_id: int) -> dict | None:
    """Read one candidate by id (read-only)."""
    row = conn.execute(
        _sa_text(f"SELECT {_CAND_COLS} FROM content_candidates WHERE id = :id"),
        {"id": int(candidate_id)},
    ).fetchone()
    return dict(row._mapping) if row is not None else None


def get_candidate_org(conn: Connection, candidate_id: int) -> str | None:
    """The owning org_id for a candidate id, regardless of status, or None if it does not
    exist. The FAIL-CLOSED IDOR primitive: the caller rejects when this returns None or an
    org != the session-authorized org (the candidate id is the auth object, never trusted)."""
    row = conn.execute(
        _sa_text("SELECT org_id FROM content_candidates WHERE id = :id"),
        {"id": int(candidate_id)},
    ).fetchone()
    return str(row[0]) if row is not None else None


def list_deck_candidates(
    conn: Connection,
    org_id: str,
    operator_handle: str,
    limit: int = 50,
    now: str | None = None,
) -> list[dict]:
    """Per-operator deck feed: PENDING candidates for ``org_id``, excluding this operator's
    dismissed rows and un-expired snoozes. Ordered null-score-last, score DESC, newest first.
    (The replies-vs-originals two-section merge is a SableWeb read-layer concern -- this
    returns the Originals stream only.)

    DELIBERATE WEB-ONLY DIVERGENCE (C0): SableWeb's ``getDeckCandidates`` additionally drops a card
    this operator SKIPPED within ~12h (a transient 'not now', read off ``content_deck_decisions``
    where decision='skip'), so a skipped card stops re-showing on refresh. That skip-window predicate
    is NOT replicated here on purpose: the Python feed serves no live operator surface (Slopper
    ``deck.py`` exposes no operator-feed route). Mirror it here only if/when a Discord/community feed
    reuses this function."""
    rows = conn.execute(
        _sa_text(
            f"SELECT c.id, c.org_id, c.kind, c.status, c.target_handle, c.payload_json, "
            f"  c.media_content_id, c.source, c.score, c.score_reason, c.tell_score, "
            f"  c.dedupe_key, c.expires_at, c.created_at "
            "FROM content_candidates c "
            "LEFT JOIN content_deck_operator_state s "
            "  ON s.candidate_id = c.id AND s.operator_handle = :op "
            "WHERE c.org_id = :org "
            "  AND c.status = 'pending' "
            "  AND COALESCE(s.state, '') != 'dismissed' "
            "  AND NOT (COALESCE(s.state, '') = 'snoozed' "
            "           AND s.snooze_until IS NOT NULL AND s.snooze_until > :now) "
            "ORDER BY (CASE WHEN c.score IS NULL THEN 1 ELSE 0 END), c.score DESC, "
            "         c.created_at DESC, c.id DESC "
            "LIMIT :limit"
        ),
        {"org": org_id, "op": operator_handle, "now": now or _utc_now_iso(), "limit": int(limit)},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def set_candidate_status(
    conn: Connection, *, candidate_id: int, org_id: str, status: str,
    expected_status: str | None = None,
) -> bool:
    """Org-scoped status flip (kept/scheduled/posted/rejected). Returns whether a row changed.
    Org-scoped in the WHERE so a wrong-org id is a no-op (the caller still pre-checks via
    get_candidate_org for a hard 403). When ``expected_status`` is given the flip is CONDITIONAL
    (``AND status = :expected``) — e.g. the keep-render revert passes ``expected_status='kept'``
    so it only ever undoes ITS OWN claim and can't clobber a concurrent legitimate transition.
    The caller MUST be in an immediate_txn."""
    where = "WHERE id = :id AND org_id = :org"
    params: dict = {"status": status, "id": int(candidate_id), "org": org_id}
    if expected_status is not None:
        where += " AND status = :expected"
        params["expected"] = expected_status
    result = conn.execute(
        _sa_text(f"UPDATE content_candidates SET status = :status {where}"), params,
    )
    return (result.rowcount or 0) > 0


def set_candidate_media(
    conn: Connection, *, candidate_id: int, org_id: str,
    media_content_id: str | None, status: str | None = None,
) -> bool:
    """Org-scoped: stamp the rendered media ref (the R2 ref) on a candidate, optionally flipping
    ``status`` in the same write (e.g. 'kept'). Returns whether a row changed. Org-scoped in the
    WHERE so a wrong-org id is a no-op (the caller still pre-checks via get_candidate_org for a
    hard 403). The keep-time render handler is the writer. Caller MUST be in an immediate_txn."""
    sets = "media_content_id = :media"
    params: dict = {"media": media_content_id, "id": int(candidate_id), "org": org_id}
    if status is not None:
        sets += ", status = :status"
        params["status"] = status
    result = conn.execute(
        _sa_text(f"UPDATE content_candidates SET {sets} WHERE id = :id AND org_id = :org"),
        params,
    )
    return (result.rowcount or 0) > 0


def claim_pending_candidate(conn: Connection, *, candidate_id: int, org_id: str,
                            claimed_status: str = "kept") -> bool:
    """Single-flight CLAIM for keep-time render: atomically move a candidate from 'pending' to
    ``claimed_status`` ('kept'). Only the FIRST concurrent request wins — a second sees a
    non-'pending' status and gets rowcount 0. Org-scoped (wrong-org id is a no-op). Returns
    whether THIS call won the claim. The keep handler reverts to 'pending' if the (paid) render
    then fails, so a lost render is retryable. Caller MUST be in an immediate_txn."""
    result = conn.execute(
        _sa_text(
            "UPDATE content_candidates SET status = :claimed "
            "WHERE id = :id AND org_id = :org AND status = 'pending'"
        ),
        {"claimed": claimed_status, "id": int(candidate_id), "org": org_id},
    )
    return (result.rowcount or 0) > 0


def get_deck_duel_pair(
    conn: Connection,
    org_id: str,
    *,
    surface: str = "discord",
    window_hours: int = 12,
    kinds: tuple[str, ...] | None = None,
    lang: str | None = None,
    include_untagged_lang: bool = False,
    require_terms: tuple[str, ...] | None = None,
    now: str | None = None,
) -> list[dict]:
    """Pick up to TWO status='pending' candidates for a COMMUNITY A/B duel (Phase 5) —
    the Python sibling of SableWeb's ``getDeckDuelPair``. Excludes any candidate that
    already appeared in a duel on this ``surface`` within ``window_hours`` (org-level —
    one Discord duel serves MANY voters, so the exclusion is per-surface, not per-actor,
    stopping the same pair from re-posting all day while still letting a card collect
    more votes later). The three filters implement per-channel content routing (see the
    bot's ``duel_channels`` config): ``kinds`` restricts to those candidate kinds (e.g. a
    meme channel → ``('meme',)``); ``lang`` restricts to ``payload_json.lang`` (a Chinese
    channel → ``'zh'``), with ``include_untagged_lang`` also admitting null-lang rows for
    the default bucket; ``require_terms`` restricts to candidates whose ``payload_json.text``
    contains AT LEAST ONE term, case-insensitive (a Prometheus channel → ``('prometheus',)``).
    All ``None`` = no filter (byte-identical pre-filter SQL). RANDOM order so pairs vary.
    Returns 0/1/2 rows (the caller degrades to "not enough cards"). Read-only; no cost."""
    if now is None:
        now = _utc_now_iso()
    try:
        window_start = (
            datetime.fromisoformat(now.replace("Z", "+00:00")) - timedelta(hours=window_hours)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        window_start = now
    params: dict = {"org": org_id, "surface": surface, "since": window_start}
    kind_clause = ""
    if kinds:
        placeholders = ", ".join(f":kind_{i}" for i in range(len(kinds)))
        kind_clause = f"  AND c.kind IN ({placeholders}) "
        params.update({f"kind_{i}": k for i, k in enumerate(kinds)})
    lang_clause = ""
    if lang:
        # dialect-portable JSON extraction (SQLite json_extract vs Postgres ->>).
        lang_expr = json_extract_text("payload_json", "lang", get_dialect(conn))
        if include_untagged_lang:
            lang_clause = f"  AND ({lang_expr} = :lang OR {lang_expr} IS NULL) "
        else:
            lang_clause = f"  AND {lang_expr} = :lang "
        params["lang"] = lang
    term_clause = ""
    if require_terms:
        # topic routing — the tweet TEXT must contain at least one term (case-insensitive
        # LIKE, OR'd). Dialect-portable text extraction; terms are bound params (never
        # interpolated) so a channel-config term can't smuggle SQL.
        text_expr = json_extract_text("payload_json", "text", get_dialect(conn))
        ors = []
        for i, term in enumerate(require_terms):
            ors.append(f"LOWER({text_expr}) LIKE :term_{i}")
            params[f"term_{i}"] = f"%{str(term).lower()}%"
        term_clause = "  AND (" + " OR ".join(ors) + ") "
    rows = conn.execute(
        _sa_text(
            f"SELECT {_CAND_COLS} FROM content_candidates c "
            "WHERE c.org_id = :org AND c.status = 'pending' "
            f"{kind_clause}"
            f"{lang_clause}"
            f"{term_clause}"
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM content_deck_decisions d "
            "     WHERE d.org_id = c.org_id AND d.surface = :surface "
            "       AND d.pair_loser_id IS NOT NULL "
            "       AND (d.candidate_id = c.id OR d.pair_loser_id = c.id) "
            "       AND d.created_at > :since"
            "  ) "
            "ORDER BY RANDOM() LIMIT 2"
        ),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def has_recent_duel_vote(
    conn: Connection,
    org_id: str,
    *,
    actor: str,
    candidate_ids: tuple[int, int],
    since: str,
) -> bool:
    """Whether ``actor`` already voted on a duel involving either of ``candidate_ids``
    after ``since`` — the DURABLE one-vote-per-member guard behind the bot's in-memory
    dedup (which dies on restart). Read-only."""
    row = conn.execute(
        _sa_text(
            "SELECT 1 FROM content_deck_decisions "
            "WHERE org_id = :org AND actor = :actor AND pair_loser_id IS NOT NULL "
            "  AND created_at >= :since "
            "  AND (candidate_id IN (:a, :b) OR pair_loser_id IN (:a, :b)) "
            "LIMIT 1"
        ),
        {"org": org_id, "actor": actor, "since": since,
         "a": int(candidate_ids[0]), "b": int(candidate_ids[1])},
    ).fetchone()
    return row is not None


def get_community_duel_leaderboard(
    conn: Connection,
    org_id: str,
    *,
    limit: int = 10,
) -> list[dict]:
    """The Phase-5 taste leaderboard: per community actor, duel votes cast and how often
    their pick AGREED with the eventual operator verdict (winner ended kept/scheduled/
    posted, or the loser ended rejected — pairs with no operator verdict yet count only
    toward ``votes``). Most-votes first. Read-only; no cost column; interpretive."""
    rows = conn.execute(
        _sa_text(
            "SELECT d.actor, COUNT(*) AS votes, "
            "  SUM(CASE WHEN w.status IN ('kept','scheduled','posted') "
            "            OR l.status = 'rejected' THEN 1 ELSE 0 END) AS agreed, "
            "  SUM(CASE WHEN w.status IN ('kept','scheduled','posted','rejected') "
            "            OR l.status IN ('kept','scheduled','posted','rejected') "
            "           THEN 1 ELSE 0 END) AS decided "
            "FROM content_deck_decisions d "
            "LEFT JOIN content_candidates w ON w.id = d.candidate_id AND w.org_id = d.org_id "
            "LEFT JOIN content_candidates l ON l.id = d.pair_loser_id AND l.org_id = d.org_id "
            "WHERE d.org_id = :org AND d.actor_kind = 'community' "
            "  AND d.pair_loser_id IS NOT NULL AND d.decision = 'keep' "
            "GROUP BY d.actor ORDER BY votes DESC, agreed DESC LIMIT :limit"
        ),
        {"org": org_id, "limit": int(limit)},
    ).fetchall()
    return [
        {"actor": str(r[0]), "votes": int(r[1] or 0), "agreed": int(r[2] or 0),
         "decided": int(r[3] or 0)}
        for r in rows
    ]


def count_pending_candidates(conn: Connection, org_id: str, *, kind: str | None = None) -> int:
    """How many status='pending' candidates ``org_id`` has, optionally for one ``kind``.
    Read-only. The ambient producer's per-kind backlog gate: an org whose deck is not being
    triaged stops accumulating paid candidates instead of piling on."""
    sql = "SELECT COUNT(*) FROM content_candidates WHERE org_id = :org AND status = 'pending'"
    params: dict = {"org": org_id}
    if kind is not None:
        sql += " AND kind = :kind"
        params["kind"] = kind
    row = conn.execute(_sa_text(sql), params).fetchone()
    return int(row[0])


def expire_due_candidates(conn: Connection, *, org_id: str, now: str | None = None) -> int:
    """Soft-expire DUE candidates: flip ONLY status='pending' rows whose expires_at has
    passed to status='expired'. NEVER touches kept/scheduled/posted/rejected (round-3
    ARCH-EXPIRE-SCHED: a kept+scheduled candidate must never be auto-expired before its
    publish_at). No physical DELETE. Returns the number expired. Caller in an immediate_txn."""
    result = conn.execute(
        _sa_text(
            "UPDATE content_candidates SET status = 'expired' "
            "WHERE org_id = :org AND status = 'pending' "
            "  AND expires_at IS NOT NULL AND expires_at <= :now"
        ),
        {"org": org_id, "now": now or _utc_now_iso()},
    )
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# content_deck_decisions
# ---------------------------------------------------------------------------
def record_deck_decision(
    conn: Connection,
    *,
    candidate_id: int,
    org_id: str,
    actor: str,
    actor_kind: str,
    decision: str,
    surface: str,
    pair_loser_id: int | None = None,
    now: str | None = None,
) -> int:
    """Append a swipe/duel decision; return the new id. FAIL-CLOSED:

      * ``candidate_id`` MUST resolve to ``org_id`` (raises ValueError otherwise -- no write).
      * if ``pair_loser_id`` is set it MUST resolve to the SAME ``org_id`` (raises ValueError,
        no write) -- a cross-org loser would poison the org-scoped Elo/BT (Codex r1).

    The caller MUST be in an immediate_txn."""
    cand_org = get_candidate_org(conn, candidate_id)
    if cand_org is None or cand_org != org_id:
        raise ValueError(f"candidate {candidate_id} does not belong to org {org_id!r}")
    if pair_loser_id is not None:
        loser_org = get_candidate_org(conn, pair_loser_id)
        if loser_org is None or loser_org != org_id:
            raise ValueError(
                f"pair_loser {pair_loser_id} does not belong to org {org_id!r}"
            )

    row = conn.execute(
        _sa_text(
            "INSERT INTO content_deck_decisions "
            "  (candidate_id, org_id, actor, actor_kind, decision, surface, pair_loser_id, created_at) "
            "VALUES (:cid, :org, :actor, :akind, :decision, :surface, :loser, :now) "
            "RETURNING id"
        ),
        {
            "cid": int(candidate_id),
            "org": org_id,
            "actor": actor,
            "akind": actor_kind,
            "decision": decision,
            "surface": surface,
            "loser": pair_loser_id,
            "now": now or _utc_now_iso(),
        },
    ).fetchone()
    return int(row[0]) if row is not None else 0


def list_deck_decisions(
    conn: Connection,
    org_id: str,
    *,
    kind: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Org-scoped read of swipe decisions JOINed to their candidate, for the keep-rate /
    ideation-quality readout (read-only, NO cost, NEVER mutates).

    Returns each decision with the candidate's ``kind``, ``payload_json`` (where
    ``template_id`` / ``register_band`` live), producer ``score``, and CURRENT ``status``
    (so a consumer can reconcile a historical decision against where the candidate ended
    up — e.g. the hard-negatives reader skips a reject whose candidate was later
    reverted + kept). Optional ``kind`` filter (e.g. ``'meme'``) and ``since`` (an
    ISO-8601 inclusive lower bound on ``created_at``). Newest first.

    INNER JOIN: candidates soft-expire (never physically deleted -- masterplan DI-NEW-2), so
    every real decision still resolves to its candidate. A decision whose candidate was
    physically GC'd would be dropped from this readout (acceptable -- it carries no
    template/register to aggregate). Both sides are org-pinned (``d.org_id`` AND ``c.org_id``):
    the d==c org invariant is only enforced in Python at write time (``record_deck_decision``),
    so the second predicate is defense-in-depth against a future direct writer / backfill.

    ``pair_loser_id`` is returned so the caller can tell a **community pairwise duel**
    (``pair_loser_id`` IS NOT NULL -- the decision enum is keep/reject/skip/schedule/post, so a
    duel win is recorded as ``decision='keep'`` and is NOT distinguishable by the decision value
    alone) from an operator single-card swipe. The caller decides what to count.
    """
    sql = (
        "SELECT d.id AS decision_id, d.candidate_id, d.decision, d.surface, d.actor, "
        "  d.actor_kind, d.pair_loser_id, d.created_at, c.kind, c.payload_json, c.score, "
        "  c.status "
        "FROM content_deck_decisions d "
        "JOIN content_candidates c ON c.id = d.candidate_id "
        "WHERE d.org_id = :org AND c.org_id = :org"
    )
    params: dict = {"org": org_id}
    if kind is not None:
        sql += " AND c.kind = :kind"
        params["kind"] = kind
    if since is not None:
        sql += " AND d.created_at >= :since"
        params["since"] = since
    sql += " ORDER BY d.created_at DESC, d.id DESC"
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = int(limit)
    rows = conn.execute(_sa_text(sql), params).fetchall()
    return [dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# content_deck_operator_state
# ---------------------------------------------------------------------------
def set_operator_candidate_state(
    conn: Connection,
    *,
    candidate_id: int,
    org_id: str,
    operator_handle: str,
    state: str,
    snooze_until: str | None = None,
    now: str | None = None,
) -> None:
    """Upsert a per-operator dismiss/snooze. ``snooze_until`` is the ISO-Z wake time for a
    snooze (ignored/NULLed for a dismiss). Idempotent on (candidate_id, operator_handle).
    The caller MUST be in an immediate_txn.

    FAIL-CLOSED like its sibling writers (the operator_state table has no org_id column, so
    org cannot be enforced in the row -- it is checked against the candidate here): the
    candidate MUST resolve to ``org_id`` or this raises ValueError, closing the cross-org
    candidate-existence oracle a fail-open write would otherwise expose."""
    cand_org = get_candidate_org(conn, candidate_id)
    if cand_org is None or cand_org != org_id:
        raise ValueError(f"candidate {candidate_id} does not belong to org {org_id!r}")
    snooze = snooze_until if state == "snoozed" else None
    conn.execute(
        _sa_text(
            "INSERT INTO content_deck_operator_state "
            "  (candidate_id, operator_handle, state, snooze_until, created_at) "
            "VALUES (:cid, :op, :state, :snooze, :now) "
            "ON CONFLICT (candidate_id, operator_handle) DO UPDATE SET "
            "  state = excluded.state, snooze_until = excluded.snooze_until"
        ),
        {
            "cid": int(candidate_id),
            "op": operator_handle,
            "state": state,
            "snooze": snooze,
            "now": now or _utc_now_iso(),
        },
    )
