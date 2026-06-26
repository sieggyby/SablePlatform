"""Content Deck Phase 4 release-substrate CRUD (migration 077).

``content_publish_jobs`` -- the keep->schedule->release organ. A KEPT candidate is scheduled into
a job; a claim-due worker flips it to 'due' at ``publish_at`` for OPERATOR HAND-OFF (composeUrl +
media download -- there is NO auto-send in v1). The candidate's OWN status flips
kept->scheduled->posted; the worker lifecycle lives in ``release_state`` here.

``org_id`` is the scope wall on every accessor. NO cost column, ever. Writers require an
``immediate_txn`` (the caller commits); reads are transaction-free. Per-account publish
authorization (which handle is ALLOWED for an org) lives in the CALLER (SableWeb
composeAccountsFor/composePersonasFor) -- this layer is org-scoped DATA access only. BUT the
state-changing primitives here now FAIL CLOSED on a handle the caller did not authorize: the
caller passes the SPECIFIC handle it validated, and ``schedule_candidate`` /``mark_handed_off``/
``mark_posted``/``cancel_publish_job`` reject it unless it matches the candidate/job's stored
``target_handle`` binding (normalized). So a caller can never authorize one account but act on a job
bound to another (Phase 4 per-account re-check), even though the allow-list itself still lives in
the caller.

LOAD-BEARING SAFETY:
  * FAIL-CLOSED IDOR -- ``schedule_candidate`` rejects a candidate that does not resolve to the
    claimed org (``get_candidate_org``); the state-flip accessors are org-scoped in the WHERE;
    ``get_job_org`` is the fail-closed primitive for the caller's 403.
  * STALE GUARD -- ``claim_due_jobs`` does NOT skip a scheduled candidate whose ORIGINAL
    ``expires_at`` has passed (``expire_due_candidates`` is pending-only, so a scheduled candidate
    is never auto-expired out from under its ``publish_at``); it releases. It DOES skip + cancel a
    candidate that is no longer EXACTLY 'scheduled' (rejected/kept/gone/posted) -- never releasing
    into a job-vs-candidate status split.
  * SINGLE-FLIGHT -- the worker claims each due job with an atomic conditional UPDATE (rowcount),
    so two workers never double-release the same job.
"""
from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import text as _sa_text
from sqlalchemy.engine import Connection

from sable_platform.db.content_deck import (
    _utc_now_iso,
    get_candidate,
    get_candidate_org,
    set_candidate_status,
)

# Defense-in-depth: ``publish_at`` MUST be strict UTC ``YYYY-MM-DDTHH:MM:SSZ`` before it is stored.
# The claim-due worker compares it LEXICALLY against the same second-precision Z form, so an offset
# (`+02:00`), a naive (no zone), or a sub-second/junk value would release early or never release.
# The Slopper deck route already normalizes to this shape, but ANY caller of ``schedule_candidate``
# (incl. future ones) is re-validated here so a bad instant can never reach the store.
#
# The regex validates SHAPE only -- a calendar-IMPOSSIBLE but well-shaped value (e.g. a zero/low
# month or day like ``2099-00-01T00:00:00Z``) would PASS the regex yet, compared lexically by the
# worker, sort BEFORE a real ``now`` and EARLY-RELEASE -- exactly the failure this guard prevents.
# So ``schedule_candidate`` ALSO ``strptime``-validates the instant (mirroring Slopper's
# ``_normalize_publish_at``) and rejects an out-of-range month/day/time.
_STRICT_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Posted-tweet-id normalization (deck posted->outcome tracking). The operator Mark-posted flow may
# supply a full tweet URL or a bare id; ``mark_posted`` normalizes it to a BARE numeric id so it can
# be the JOIN key into ``relay_tweet_snapshots.tweet_x_id`` with no new schema. BEST-EFFORT: an
# absent/unparseable ref stores NULL (the post is NEVER blocked — the job just never enters the
# outcome snapshot set). STRICT so a wrong id is never stored (which would attribute performance to
# an unrelated tweet): a bare id is a FULL 5-25-digit match; a URL must be a real x.com/twitter.com
# host with a ``/status/<id>`` path segment whose id is bounded by a non-digit (so "12345abc" or a
# 26-digit run is REJECTED, not truncated), and a non-X host (example.com) is rejected.
_TWEET_ID_BARE_RE = re.compile(r"^\d{5,25}$")
_STATUS_PATH_RE = re.compile(r"/status(?:es)?/(\d{5,25})(?:/|$)")
_TWEET_HOSTS = frozenset({"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"})


def parse_tweet_id(raw: str | None) -> str | None:
    """Normalize an operator posted reference (tweet URL or bare id) to a bare numeric tweet id, or
    None. Accepts a bare 5-25-digit id, or an ``x.com``/``twitter.com`` ``/status/<id>`` URL (with a
    trailing ``/photo/N``, query, or anchor). Returns ``None`` on empty/unparseable/non-X input —
    NEVER a truncated/partial id. Pure + side-effect-free."""
    from urllib.parse import urlsplit

    if not raw:
        return None
    s = str(raw).strip()
    if _TWEET_ID_BARE_RE.match(s):
        return s
    try:
        parts = urlsplit(s if "//" in s else "https://" + s)
    except ValueError:
        return None
    if (parts.hostname or "").lower() not in _TWEET_HOSTS:
        return None  # not an x.com/twitter.com URL → never trust a /status/ id from another host
    m = _STATUS_PATH_RE.search(parts.path)
    return m.group(1) if m else None


_JOB_COLS = (
    "id, candidate_id, org_id, target_handle, release_state, publish_at, next_attempt_at, "
    "attempt_count, claimed_at, handed_off_at, posted_ref, created_at, updated_at"
)

# ``_JOB_COLS`` re-aliased to the ``j`` table alias, for the candidate JOIN in ``list_publish_jobs``
# (so the calendar feed can carry the candidate's draft/caption + media ref for operator hand-off).
_JOB_COLS_J = ", ".join(f"j.{c.strip()}" for c in _JOB_COLS.split(","))


def _norm_handle(handle: str | None) -> str:
    """Normalize an X handle for an equality check: strip, drop a leading ``@``, casefold. The
    per-account publish binding is compared normalized so ``@TIGFoundation`` == ``tigfoundation``."""
    return (handle or "").strip().lstrip("@").casefold()


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


def _candidate_status(conn: Connection, candidate_id: int, org_id: str) -> str | None:
    """The candidate's CURRENT status (org-scoped), or None. The post-claim liveness gate: a
    candidate rejected AFTER its job was claimed is no longer 'scheduled' (the claim-due cancel
    only covers the still-'scheduled' window), so hand-off/post must re-check it to avoid a
    job=posted / candidate=rejected status split (review M1)."""
    row = conn.execute(
        _sa_text("SELECT status FROM content_candidates WHERE id = :id AND org_id = :org"),
        {"id": int(candidate_id), "org": org_id},
    ).fetchone()
    return str(row[0]) if row is not None else None


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
    the new job id, or None if the candidate isn't org-owned, isn't currently 'kept', or is an
    UNBOUND draft (its STORED ``target_handle`` is NULL/blank). The candidate's STORED
    ``target_handle`` is the authoritative "publish AS" binding (masterplan §3); a null-target
    candidate is a full-ops-only draft that CANNOT graduate to publish (SEC-3 / Phase 4), so it is
    rejected here regardless of the param. The caller-supplied ``target_handle`` (the handle the
    caller authorized via the full binding pair) MUST equal that stored binding (normalized) -- a
    mismatch FAILS CLOSED (returns None), so a caller cannot authorize one account but schedule the
    job as another. The job is then bound to the candidate's OWN stored handle (never an arbitrary
    param). A blank ``target_handle`` param is still rejected up front. FAIL-CLOSED IDOR. Caller in
    immediate_txn."""
    if get_candidate_org(conn, candidate_id) != org_id:
        return None  # unknown or wrong-org -> fail closed
    if not (target_handle or "").strip():
        raise ValueError(
            "schedule_candidate: target_handle is required (a null-target candidate cannot be scheduled)"
        )
    # Defense-in-depth: re-validate the strict-UTC publish_at shape before the store (the worker
    # compares it lexically; an offset/naive/sub-second/junk value would release wrong). FAIL CLOSED
    # on the RAW value -- surrounding whitespace is REJECTED, never silently stripped: validating a
    # stripped copy while storing the raw value would let a leading space (`" 2099-...Z"`) pass (its
    # stripped form is valid) yet sort LEXICALLY BEFORE a real ISO-Z ``now`` once stored, early-
    # releasing the post. We validate -- and below, STORE -- the exact canonical string ``_pa``.
    _pa = publish_at or ""
    if _pa != _pa.strip() or not _STRICT_UTC_RE.match(_pa):
        raise ValueError(
            "schedule_candidate: publish_at must be strict UTC 'YYYY-MM-DDTHH:MM:SSZ' "
            "(offset/naive/sub-second/surrounding-whitespace/malformed values are rejected)"
        )
    # ...then confirm it is a REAL calendar instant. The regex passes a shaped-but-impossible value
    # (zero/low/over-range month/day/time); strptime rejects it so a value that would sort BEFORE a
    # real 'now' and early-release can never reach the store (mirrors Slopper _normalize_publish_at).
    try:
        datetime.strptime(_pa[:-1], "%Y-%m-%dT%H:%M:%S")  # drop trailing 'Z'
    except ValueError as exc:
        raise ValueError(
            "schedule_candidate: publish_at must be strict UTC 'YYYY-MM-DDTHH:MM:SSZ' "
            "(offset/naive/sub-second/malformed values are rejected)"
        ) from exc
    # The candidate's STORED target_handle is the authoritative publish-AS binding (§3). An
    # unbound full-ops-only draft (stored target_handle NULL/blank) cannot graduate to publish
    # (SEC-3) -- fail closed regardless of the param. Bind the job to the candidate's OWN handle
    # so the released job can never publish as an account the candidate was not produced for.
    cand = get_candidate(conn, candidate_id)
    if cand is None:
        return None
    bound_handle = cand.get("target_handle")
    if not (bound_handle or "").strip():
        return None  # unbound draft -> cannot be scheduled
    # The caller authorized a SPECIFIC handle (via the full binding pair) and passed it here. It
    # MUST equal the candidate's OWN stored binding -- otherwise the caller authorized one account
    # but the job would publish as another. Fail closed on any mismatch (SEC-3 / Phase 4).
    if _norm_handle(target_handle) != _norm_handle(bound_handle):
        return None
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
        {"cid": int(candidate_id), "org": org_id, "th": bound_handle, "pa": _pa, "now": now},
    ).fetchone()
    if row is None:
        # INSERT...RETURNING must yield a row; a None here would commit an orphaned 'scheduled'
        # candidate (no job, un-reschedulable, un-cancelable). Fail hard so the txn rolls back (L2).
        raise RuntimeError("schedule_candidate: INSERT ... RETURNING id yielded no row")
    return int(row[0])


def claim_due_jobs(conn: Connection, *, now: str | None = None, limit: int = 50) -> list[dict]:
    """The claim-due worker: flip SCHEDULED jobs whose ``publish_at <= now`` to 'due' for operator
    hand-off. SINGLE-FLIGHT (atomic conditional UPDATE per job -> rowcount), so two workers never
    double-claim. STALE GUARD: a scheduled candidate past its ORIGINAL ``expires_at`` STILL releases
    (no ``expires_at`` check -- ``expire_due_candidates`` is pending-only). LIVENESS GATE: the
    candidate must be EXACTLY 'scheduled' to release -- a candidate that is gone, rejected, or flipped
    to any OTHER state (kept/pending/posted, e.g. a stale swipe clobbered it) is SKIPPED and its job
    CANCELED (never released into a job-vs-candidate status split). RETRY GATE: a job carrying a future ``next_attempt_at`` (a
    backoff timestamp set after a failed release attempt) is NOT yet eligible even if ``publish_at``
    is in the past -- the masterplan future-gates retries on ``next_attempt_at <= now`` (mirrors the
    relay publication-job claim). Returns the jobs newly flipped to 'due'. Caller in immediate_txn."""
    now = now or _utc_now_iso()
    due = conn.execute(
        _sa_text(
            f"SELECT {_JOB_COLS} FROM content_publish_jobs "
            "WHERE release_state = 'scheduled' AND publish_at <= :now "
            "AND (next_attempt_at IS NULL OR next_attempt_at <= :now) "
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
        if cand is None or str(cand[0]) != "scheduled":
            # The candidate must be EXACTLY 'scheduled' to release. Gone, rejected, OR flipped to any
            # other state (kept/pending/posted — e.g. a stale-tab swipe or forged decide clobbered it
            # out from under the job) -> cancel the job, never release into a status split (M1,
            # hardened: a job=due/posted while candidate!=scheduled would break hand-off/post).
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


def count_due_jobs(conn: Connection, *, now: str | None = None) -> int:
    """Count SCHEDULED jobs currently eligible for ``claim_due_jobs`` (``publish_at <= now`` with the
    ``next_attempt_at`` backoff gate passed). The claim-drain loop's TERMINATION signal: ``claim_due_jobs``
    returns ONLY the jobs it FLIPPED to 'due' -- a since-rejected candidate's job is CANCELED and
    EXCLUDED -- so an empty claim batch cannot be read as "no more due rows" (a full ``limit``-sized batch
    of all-canceled jobs comes back ``[]``). Reusing the SAME gate here lets the drain stop only when the
    scheduled-due set is truly empty, instead of trusting the (lossy) claimed count. Read-only -- the
    caller does NOT need to be inside a write txn, though the drain calls it inside the claim's
    ``immediate_txn`` so the count reflects the rows that batch already flipped/canceled."""
    now = now or _utc_now_iso()
    row = conn.execute(
        _sa_text(
            "SELECT COUNT(*) FROM content_publish_jobs "
            "WHERE release_state = 'scheduled' AND publish_at <= :now "
            "AND (next_attempt_at IS NULL OR next_attempt_at <= :now)"
        ),
        {"now": now},
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def mark_handed_off(
    conn: Connection,
    *,
    job_id: int,
    org_id: str,
    authorized_target_handle: str | None,
    now: str | None = None,
) -> bool:
    """Operator opened the composeUrl hand-off: release_state 'due' -> 'handed_off'. Org-scoped +
    conditional (only its own 'due' state). REFUSES if the candidate is no longer 'scheduled' (e.g.
    rejected after the job was claimed -- review M1: never hand off a killed candidate). Also
    REFUSES unless ``authorized_target_handle`` matches the job's stored ``target_handle``
    (normalized) -- the caller MUST prove it authorized THIS job's per-account publish binding
    (Phase 4 per-account re-check), so a session scoped to a different account/persona cannot drive
    the hand-off with only ``job_id`` + ``org_id``. Returns whether a row changed. immediate_txn."""
    job = get_publish_job(conn, job_id)
    if job is None or job["org_id"] != org_id:
        return False
    if _norm_handle(authorized_target_handle) != _norm_handle(job["target_handle"]):
        return False  # caller did not authorize this job's publish-AS binding -> fail closed
    if _candidate_status(conn, int(job["candidate_id"]), org_id) != "scheduled":
        return False
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
    authorized_target_handle: str | None,
    posted_ref: str | None = None,
    now: str | None = None,
) -> bool:
    """Operator confirmed posted: release_state 'due'/'handed_off' -> 'posted' (+ ``posted_ref``),
    and the candidate status -> 'posted'. Org-scoped. REFUSES unless ``authorized_target_handle``
    matches the job's stored ``target_handle`` (normalized) -- the caller MUST prove it authorized
    THIS job's per-account publish binding (Phase 4 per-account re-check), so a session scoped to a
    different account/persona cannot post with only ``job_id`` + ``org_id``. Returns whether the job
    changed. immediate_txn."""
    job = get_publish_job(conn, job_id)
    if job is None or job["org_id"] != org_id:
        return False
    if _norm_handle(authorized_target_handle) != _norm_handle(job["target_handle"]):
        return False  # caller did not authorize this job's publish-AS binding -> fail closed
    # Fail closed if the candidate is no longer 'scheduled' (e.g. rejected after the job was claimed)
    # -- never post into a job whose candidate was killed (review M1: avoid a posted/rejected split).
    if _candidate_status(conn, int(job["candidate_id"]), org_id) != "scheduled":
        return False
    now = now or _utc_now_iso()
    # Normalize the posted reference to a BARE tweet id so it can JOIN relay_tweet_snapshots for the
    # outcome panel. Best-effort: unparseable/absent -> NULL (never blocks the post; just untracked).
    posted_ref = parse_tweet_id(posted_ref)
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
    # The candidate was verified 'scheduled' above and we are in the caller's serialized
    # immediate_txn, so this conditional flip is guaranteed to land (no silent divergence).
    set_candidate_status(
        conn, candidate_id=int(job["candidate_id"]), org_id=org_id,
        status="posted", expected_status="scheduled",
    )
    return True


def get_deck_posted_performance(conn: Connection, org_id: str) -> dict:
    """The audience-OUTCOME rollup for posted deck content (the Content-Quality dashboard panel).

    JOINs each posted job (release_state='posted' with a bare-id ``posted_ref``) to its candidate KIND
    and to the SHARED ``relay_tweet_snapshots`` 24h/'ok' reading. Returns, per org and per kind, the
    posted count, how many have MATURED (a 24h reading exists), the MATURING count (posted but not yet
    24h-measured), and the mean 24h engagement (likes+retweets+replies — the campaign-rollup ``.total``,
    NOT views) over matured rows only (null when none matured). MATURED-ONLY: a tweet < 24h old simply
    has no 24h row and is reported as maturing, NEVER counted as zero. NO cost column. Org-scoped.

    Degrades to all-zero/empty if the tables are absent — the caller wraps it best-effort so an old db
    never 500s the dashboard.
    """
    rows = conn.execute(
        _sa_text(
            "SELECT j.posted_ref AS ref, c.kind AS kind, s.status AS snap_status, "
            "  COALESCE(s.likes, 0) + COALESCE(s.retweets, 0) + COALESCE(s.replies, 0) AS engagement "
            "FROM content_publish_jobs j "
            "LEFT JOIN content_candidates c ON c.id = j.candidate_id AND c.org_id = j.org_id "
            "LEFT JOIN relay_tweet_snapshots s "
            "  ON s.tweet_x_id = j.posted_ref AND s.target_age_hours = 24 "
            "WHERE j.org_id = :org AND j.release_state = 'posted' "
            "  AND j.posted_ref IS NOT NULL AND j.posted_ref <> ''"
        ),
        {"org": org_id},
    ).fetchall()

    # DEDUP by distinct posted_ref: an operator could Mark-posted two different jobs with the SAME
    # tweet — count the TWEET once (else measured/the mean inflate). The snapshot due-query dedups so
    # there is normally ≤1 24h row per tweet, but relay_tweet_snapshots has no UNIQUE on
    # (tweet_x_id, target_age_hours), so to stay DETERMINISTIC under a duplicate row we PREFER an 'ok'
    # reading over any non-'ok' (deleted/null) — a real measurement always wins. Index a
    # CompatConnection row by POSITION (the documented unpack gotcha).
    seen: dict[str, dict] = {}
    for r in rows:
        ref = str(r[0])
        status = (r[2] or None)
        cur = seen.get(ref)
        if cur is None or (cur["status"] != "ok" and status == "ok"):
            seen[ref] = {"kind": str(r[1] or "(unset)"), "status": status, "eng": int(r[3] or 0)}

    posted_count = len(seen)
    measured_eng: list[int] = []
    deleted_count = 0
    by_kind: dict[str, dict] = {}
    for v in seen.values():
        k = by_kind.setdefault(v["kind"], {"posted": 0, "measured": 0, "eng_sum": 0})
        k["posted"] += 1
        if v["status"] == "ok":  # a MATURED 24h reading (a (0,0,0) reading is a real measured zero)
            k["measured"] += 1
            k["eng_sum"] += v["eng"]
            measured_eng.append(v["eng"])
        elif v["status"] == "deleted":  # 404'd — TERMINAL, never "maturing forever"
            deleted_count += 1

    measured_count = len(measured_eng)
    avg_engagement = (sum(measured_eng) / measured_count) if measured_count else None
    by_kind_out = [
        {
            "kind": kind,
            "posted": v["posted"],
            "measured": v["measured"],
            "avg_engagement": (v["eng_sum"] / v["measured"]) if v["measured"] else None,
        }
        for kind, v in sorted(
            by_kind.items(),
            key=lambda kv: (-(kv[1]["eng_sum"] / kv[1]["measured"]) if kv[1]["measured"] else 1.0, kv[0]),
        )
    ]
    return {
        "posted_count": posted_count,
        "measured_count": measured_count,
        "avg_engagement": avg_engagement,
        # posted but neither measured nor deleted (still maturing) — a gone tweet is terminal, excluded.
        "maturing_count": posted_count - measured_count - deleted_count,
        "by_kind": by_kind_out,
    }


def cancel_publish_job(
    conn: Connection,
    *,
    job_id: int,
    org_id: str,
    authorized_target_handle: str | None,
    now: str | None = None,
) -> bool:
    """Operator cancels a not-yet-posted job: release_state 'scheduled'/'due'/'handed_off' ->
    'canceled', and the candidate goes BACK to 'kept' (re-schedulable). Org-scoped. REFUSES unless
    ``authorized_target_handle`` matches the job's stored ``target_handle`` (normalized) -- the caller
    MUST prove it authorized THIS job's per-account publish binding (Phase 4 per-account re-check),
    exactly like ``mark_handed_off``/``mark_posted``. Without it a session scoped to a DIFFERENT
    account/persona could revert another operator's scheduled job with only ``job_id`` + ``org_id``
    (the candidate flips back to 'kept' + re-enters the deck), bypassing the per-account wall that
    every OTHER canonical state-flip already enforces. Returns whether a row changed. immediate_txn."""
    job = get_publish_job(conn, job_id)
    if job is None or job["org_id"] != org_id:
        return False
    if _norm_handle(authorized_target_handle) != _norm_handle(job["target_handle"]):
        return False  # caller did not authorize this job's publish-AS binding -> fail closed
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
    first. Defaults to the LIVE states; pass ``states=('posted',)`` for history. Empty ``states`` -> [].

    Each row JOINs its candidate to also carry ``candidate_payload_json`` (the draft/caption text),
    ``candidate_media_content_id`` (the rendered-media R2 ref), and ``candidate_kind`` (the content
    kind: meme/tweet/thread/quote_card/clip/copypasta) so the calendar surface can build the
    operator HAND-OFF affordance (composeUrl + media download) the masterplan requires for a 'due'
    job. INNER JOIN is safe: a job's candidate always exists (FK; candidates soft-expire, never
    physically delete, and a physical GC cascades the job). NO cost column is ever selected."""
    if not states:
        return []
    keys = [f":s{i}" for i in range(len(states))]
    params: dict = {"org": org_id, "limit": int(limit)}
    for i, s in enumerate(states):
        params[f"s{i}"] = s
    rows = conn.execute(
        _sa_text(
            f"SELECT {_JOB_COLS_J}, "
            "  c.payload_json AS candidate_payload_json, "
            "  c.media_content_id AS candidate_media_content_id, "
            "  c.kind AS candidate_kind "
            "FROM content_publish_jobs j "
            "JOIN content_candidates c ON c.id = j.candidate_id "
            f"WHERE j.org_id = :org AND j.release_state IN ({', '.join(keys)}) "
            "ORDER BY j.publish_at, j.id LIMIT :limit"
        ),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]
