"""Content Deck Phase 4 release-substrate CRUD (migration 077).

``content_publish_jobs`` -- the keep->schedule->release organ. A KEPT candidate is scheduled into
a job; a claim-due worker flips it to 'due' at ``publish_at`` for OPERATOR HAND-OFF (composeUrl +
media download -- there is NO auto-send in v1). The candidate's OWN status flips
kept->scheduled->posted; the worker lifecycle lives in ``release_state`` here.

``org_id`` is the scope wall on every accessor. NO cost column, ever. Writers require an
``immediate_txn`` (the caller commits); reads are transaction-free. Per-account publish
authorization (``target_handle``) lives in the CALLER (SableWeb composeAccountsFor/
composePersonasFor) -- this layer is org-scoped DATA access only.

LOAD-BEARING SAFETY:
  * FAIL-CLOSED IDOR -- ``schedule_candidate`` rejects a candidate that does not resolve to the
    claimed org (``get_candidate_org``); the state-flip accessors are org-scoped in the WHERE;
    ``get_job_org`` is the fail-closed primitive for the caller's 403.
  * STALE GUARD -- ``claim_due_jobs`` does NOT skip a scheduled candidate whose ORIGINAL
    ``expires_at`` has passed (``expire_due_candidates`` is pending-only, so a scheduled candidate
    is never auto-expired out from under its ``publish_at``); it releases. It DOES skip + cancel a
    since-REJECTED candidate.
  * SINGLE-FLIGHT -- the worker claims each due job with an atomic conditional UPDATE (rowcount),
    so two workers never double-release the same job.
"""
from __future__ import annotations

from sqlalchemy import text as _sa_text
from sqlalchemy.engine import Connection

from sable_platform.db.content_deck import (
    _utc_now_iso,
    get_candidate_org,
    set_candidate_status,
)

_JOB_COLS = (
    "id, candidate_id, org_id, target_handle, release_state, publish_at, next_attempt_at, "
    "attempt_count, claimed_at, handed_off_at, posted_ref, created_at, updated_at"
)


def get_job_org(conn: Connection, job_id: int) -> str | None:
    """Owning ``org_id`` for a publish-job id, regardless of state, or None. The FAIL-CLOSED IDOR
    primitive: the caller rejects when this is None or != the session-authorized org."""
    row = conn.execute(
        _sa_text("SELECT org_id FROM content_publish_jobs WHERE id = :id"),
        {"id": int(job_id)},
    ).fetchone()
    return str(row[0]) if row is not None else None


def get_publish_job(conn: Connection, job_id: int) -> dict | None:
    row = conn.execute(
        _sa_text(f"SELECT {_JOB_COLS} FROM content_publish_jobs WHERE id = :id"),
        {"id": int(job_id)},
    ).fetchone()
    return dict(row._mapping) if row is not None else None


def schedule_candidate(
    conn: Connection,
    *,
    candidate_id: int,
    org_id: str,
    target_handle: str,
    publish_at: str,
    now: str | None = None,
) -> int | None:
    """Schedule a KEPT candidate for release at ``publish_at``. Creates a content_publish_job
    (release_state='scheduled') AND flips the candidate kept->scheduled in the same txn. Returns
    the new job id, or None if the candidate isn't org-owned or isn't currently 'kept'. Requires a
    non-empty ``target_handle`` (a null-target candidate cannot be scheduled -- masterplan SEC-3).
    FAIL-CLOSED IDOR. Caller in immediate_txn."""
    if get_candidate_org(conn, candidate_id) != org_id:
        return None  # unknown or wrong-org -> fail closed
    if not (target_handle or "").strip():
        raise ValueError(
            "schedule_candidate: target_handle is required (a null-target candidate cannot be scheduled)"
        )
    now = now or _utc_now_iso()
    # CONDITIONAL flip: only its OWN 'kept' state -> 'scheduled'. A non-kept candidate (already
    # scheduled/posted/rejected) is a no-op and NO job is created (idempotent double-schedule guard).
    if not set_candidate_status(
        conn, candidate_id=candidate_id, org_id=org_id, status="scheduled", expected_status="kept"
    ):
        return None
    row = conn.execute(
        _sa_text(
            "INSERT INTO content_publish_jobs "
            "  (candidate_id, org_id, target_handle, release_state, publish_at, created_at, updated_at) "
            "VALUES (:cid, :org, :th, 'scheduled', :pa, :now, :now) RETURNING id"
        ),
        {"cid": int(candidate_id), "org": org_id, "th": target_handle, "pa": publish_at, "now": now},
    ).fetchone()
    return int(row[0]) if row is not None else None


def claim_due_jobs(conn: Connection, *, now: str | None = None, limit: int = 50) -> list[dict]:
    """The claim-due worker: flip SCHEDULED jobs whose ``publish_at <= now`` to 'due' for operator
    hand-off. SINGLE-FLIGHT (atomic conditional UPDATE per job -> rowcount), so two workers never
    double-claim. STALE GUARD: a scheduled candidate past its ORIGINAL ``expires_at`` STILL releases
    (no ``expires_at`` check -- ``expire_due_candidates`` is pending-only). A since-REJECTED candidate
    is SKIPPED and its job CANCELED. Returns the jobs newly flipped to 'due'. Caller in immediate_txn."""
    now = now or _utc_now_iso()
    due = conn.execute(
        _sa_text(
            f"SELECT {_JOB_COLS} FROM content_publish_jobs "
            "WHERE release_state = 'scheduled' AND publish_at <= :now "
            "ORDER BY publish_at, id LIMIT :limit"
        ),
        {"now": now, "limit": int(limit)},
    ).fetchall()
    claimed: list[dict] = []
    for r in due:
        job = dict(r._mapping)
        cand = conn.execute(
            _sa_text("SELECT status FROM content_candidates WHERE id = :id AND org_id = :org"),
            {"id": int(job["candidate_id"]), "org": job["org_id"]},
        ).fetchone()
        if cand is None or str(cand[0]) == "rejected":
            # candidate gone or rejected after scheduling -> cancel the job, never release.
            conn.execute(
                _sa_text(
                    "UPDATE content_publish_jobs SET release_state = 'canceled', updated_at = :now "
                    "WHERE id = :id AND release_state = 'scheduled'"
                ),
                {"now": now, "id": int(job["id"])},
            )
            continue
        res = conn.execute(
            _sa_text(
                "UPDATE content_publish_jobs SET release_state = 'due', claimed_at = :now, "
                "updated_at = :now WHERE id = :id AND release_state = 'scheduled'"
            ),
            {"now": now, "id": int(job["id"])},
        )
        if (res.rowcount or 0) > 0:  # single-flight winner
            job["release_state"], job["claimed_at"] = "due", now
            claimed.append(job)
    return claimed


def mark_handed_off(conn: Connection, *, job_id: int, org_id: str, now: str | None = None) -> bool:
    """Operator opened the composeUrl hand-off: release_state 'due' -> 'handed_off'. Org-scoped +
    conditional (only its own 'due' state). Returns whether a row changed. Caller in immediate_txn."""
    now = now or _utc_now_iso()
    res = conn.execute(
        _sa_text(
            "UPDATE content_publish_jobs SET release_state = 'handed_off', handed_off_at = :now, "
            "updated_at = :now WHERE id = :id AND org_id = :org AND release_state = 'due'"
        ),
        {"now": now, "id": int(job_id), "org": org_id},
    )
    return (res.rowcount or 0) > 0


def mark_posted(
    conn: Connection,
    *,
    job_id: int,
    org_id: str,
    posted_ref: str | None = None,
    now: str | None = None,
) -> bool:
    """Operator confirmed posted: release_state 'due'/'handed_off' -> 'posted' (+ ``posted_ref``),
    and the candidate status -> 'posted'. Org-scoped. Returns whether the job changed. immediate_txn."""
    job = get_publish_job(conn, job_id)
    if job is None or job["org_id"] != org_id:
        return False
    now = now or _utc_now_iso()
    res = conn.execute(
        _sa_text(
            "UPDATE content_publish_jobs SET release_state = 'posted', posted_ref = :ref, "
            "updated_at = :now WHERE id = :id AND org_id = :org "
            "AND release_state IN ('due', 'handed_off')"
        ),
        {"now": now, "ref": posted_ref, "id": int(job_id), "org": org_id},
    )
    if (res.rowcount or 0) == 0:
        return False
    set_candidate_status(
        conn, candidate_id=int(job["candidate_id"]), org_id=org_id,
        status="posted", expected_status="scheduled",
    )
    return True


def cancel_publish_job(conn: Connection, *, job_id: int, org_id: str, now: str | None = None) -> bool:
    """Operator cancels a not-yet-posted job: release_state 'scheduled'/'due'/'handed_off' ->
    'canceled', and the candidate goes BACK to 'kept' (re-schedulable). Org-scoped. immediate_txn."""
    job = get_publish_job(conn, job_id)
    if job is None or job["org_id"] != org_id:
        return False
    now = now or _utc_now_iso()
    res = conn.execute(
        _sa_text(
            "UPDATE content_publish_jobs SET release_state = 'canceled', updated_at = :now "
            "WHERE id = :id AND org_id = :org AND release_state IN ('scheduled', 'due', 'handed_off')"
        ),
        {"now": now, "id": int(job_id), "org": org_id},
    )
    if (res.rowcount or 0) == 0:
        return False
    set_candidate_status(
        conn, candidate_id=int(job["candidate_id"]), org_id=org_id,
        status="kept", expected_status="scheduled",
    )
    return True


def list_publish_jobs(
    conn: Connection,
    org_id: str,
    *,
    states: tuple[str, ...] = ("scheduled", "due", "handed_off"),
    limit: int = 200,
) -> list[dict]:
    """The content-calendar feed: an org's publish jobs in the given ``release_states``, soonest
    first. Defaults to the LIVE states; pass ``states=('posted',)`` for history. Empty ``states`` -> []."""
    if not states:
        return []
    keys = [f":s{i}" for i in range(len(states))]
    params: dict = {"org": org_id, "limit": int(limit)}
    for i, s in enumerate(states):
        params[f"s{i}"] = s
    rows = conn.execute(
        _sa_text(
            f"SELECT {_JOB_COLS} FROM content_publish_jobs "
            f"WHERE org_id = :org AND release_state IN ({', '.join(keys)}) "
            "ORDER BY publish_at, id LIMIT :limit"
        ),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]
