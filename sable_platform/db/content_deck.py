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

from datetime import datetime, timezone

from sqlalchemy import text as _sa_text
from sqlalchemy.engine import Connection

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
    returns the Originals stream only.)"""
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
