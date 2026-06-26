"""Migration 077 — Content Deck Phase 4 release-substrate CRUD tests.

Two layers (mirrors test_content_deck.py):
  * Behavioral (``sa_conn``) — the keep->schedule->release lifecycle, the masterplan's required
    cases: schedule only a KEPT candidate, FAIL-CLOSED IDOR (wrong-org candidate), the claim-due
    worker (publish_at gate + single-flight), the STALE GUARD (a scheduled candidate past its
    ORIGINAL expires_at still releases) + the SINCE-REJECTED guard (canceled, not released),
    hand-off/post/cancel state machine, and per-accessor org-scoping.
  * SQL-path (raw sqlite3 + ensure_schema) — the ``077_*.sql`` release_state CHECK + the
    candidate_id ON DELETE CASCADE, so a typo'd enum or dropped CASCADE in the .sql can't pass
    green while only schema.py is right.
"""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from sable_platform.db import content_deck as cd
from sable_platform.db import content_publish as cp
from sable_platform.db.connection import ensure_schema
from sable_platform.relay.bot.txn import immediate_txn

PAST = "2020-01-01T00:00:00Z"
NOW = "2025-01-01T00:00:00Z"
FUTURE = "2099-01-01T00:00:00Z"


def _seed(conn, *orgs):
    for o in orgs or ("orgA",):
        conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": o})


def _kept(conn, *, org="orgA", target="@tigfoundation", expires_at=None):
    """A KEPT candidate ready to schedule."""
    cid = cd.upsert_candidate(
        conn, org_id=org, kind="meme", payload_json='{"template_id":"drake"}',
        source="seed", target_handle=target, expires_at=expires_at,
    )
    assert cd.set_candidate_status(conn, candidate_id=cid, org_id=org, status="kept",
                                   expected_status="pending")
    return cid


# === Behavioral (schema.py path) ============================================

def test_schedule_kept_candidate_creates_job_and_flips_status(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn)
        job_id = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                       target_handle="@tigfoundation", publish_at=FUTURE)
    assert job_id and job_id > 0
    assert cd.get_candidate(sa_conn, cid)["status"] == "scheduled"   # candidate flipped
    job = cp.get_publish_job(sa_conn, job_id)
    assert job["release_state"] == "scheduled" and job["org_id"] == "orgA"
    assert job["target_handle"] == "@tigfoundation" and job["publish_at"] == FUTURE
    assert cp.get_job_org(sa_conn, job_id) == "orgA"
    assert cp.get_job_org(sa_conn, 999999) is None  # fail-closed primitive


def test_schedule_fails_closed_on_wrong_org(sa_conn):
    _seed(sa_conn, "orgA", "orgB"); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, org="orgA")
        # orgB tries to schedule orgA's candidate -> None, no job, candidate unchanged
        job = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgB",
                                    target_handle="@x", publish_at=FUTURE)
    assert job is None
    assert cd.get_candidate(sa_conn, cid)["status"] == "kept"
    assert cp.list_publish_jobs(sa_conn, "orgB") == []


def test_schedule_non_kept_candidate_is_noop(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = cd.upsert_candidate(sa_conn, org_id="orgA", kind="meme",
                                  payload_json="{}", source="s", target_handle="@h")  # PENDING
        job = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@h", publish_at=FUTURE)
    assert job is None  # only a 'kept' candidate can be scheduled
    assert cd.get_candidate(sa_conn, cid)["status"] == "pending"


def test_schedule_requires_target_handle(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn)
        with pytest.raises(ValueError, match="target_handle"):
            cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                  target_handle="  ", publish_at=FUTURE)


@pytest.mark.parametrize("bad_pa", [
    "2099-01-01T14:00:00+02:00",   # offset zone (not UTC)
    "2099-01-01T14:00:00",          # naive (no zone)
    "2099-01-01T14:00:00.123Z",     # sub-second precision (lexical compare skew)
    "2099-01-01 14:00:00Z",         # space separator, not 'T'
    "not-a-date",                   # junk
])
def test_schedule_rejects_non_strict_utc_publish_at(sa_conn, bad_pa):
    """Defense-in-depth: ``schedule_candidate`` re-validates the strict-UTC publish_at shape before
    the store (the claim-due worker compares it lexically) -- a non-Z/naive/sub-second/junk value is
    rejected with a ValueError and no job is created."""
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn)
        with pytest.raises(ValueError, match="publish_at"):
            cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                  target_handle="@tigfoundation", publish_at=bad_pa)
    assert cd.get_candidate(sa_conn, cid)["status"] == "kept"  # not flipped to scheduled
    assert cp.list_publish_jobs(sa_conn, "orgA") == []


@pytest.mark.parametrize("bad_pa", [
    "2099-00-01T00:00:00Z",   # month 00 -> sorts BEFORE a real 'now' lexically -> EARLY-RELEASE
    "2099-01-00T00:00:00Z",   # day 00 -> same early-release direction
    "2099-13-01T00:00:00Z",   # impossible month 13
    "2099-01-32T00:00:00Z",   # impossible day 32
    "2099-01-01T25:00:00Z",   # impossible hour 25
])
def test_schedule_rejects_calendar_impossible_publish_at(sa_conn, bad_pa):
    """The strict-UTC regex validates SHAPE only: a calendar-impossible value with a zero/low month
    or day (e.g. ``2099-00-01T00:00:00Z``) PASSES the regex yet, compared LEXICALLY by the claim-due
    worker, sorts BEFORE a real ``now`` and would EARLY-RELEASE -- the exact failure the guard claims
    to prevent. ``schedule_candidate`` strptime-validates the instant after the regex, so such a
    value is rejected with a ValueError and NO job is created (locks the early-release direction)."""
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn)
        with pytest.raises(ValueError, match="publish_at"):
            cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                  target_handle="@tigfoundation", publish_at=bad_pa)
    assert cd.get_candidate(sa_conn, cid)["status"] == "kept"  # not flipped to scheduled
    assert cp.list_publish_jobs(sa_conn, "orgA") == []


@pytest.mark.parametrize("bad_pa", [
    " 2099-01-01T00:00:00Z",    # LEADING space -> sorts BEFORE a real 'now' lexically -> early-release
    "2099-01-01T00:00:00Z ",    # trailing space
    "\t2099-01-01T00:00:00Z",   # leading tab
    "2099-01-01T00:00:00Z\n",   # trailing newline
])
def test_schedule_rejects_surrounding_whitespace_publish_at(sa_conn, bad_pa):
    """The boundary must FAIL CLOSED on the RAW value, not a stripped copy: validating the stripped
    publish_at while STORING the raw value would let a leading-space ``" 2099-...Z"`` pass (its
    stripped form is strict-UTC) yet, once stored, sort LEXICALLY BEFORE a real ISO-Z ``now`` (0x20
    < '2') so the claim-due worker EARLY-RELEASES it. ``schedule_candidate`` rejects any surrounding
    whitespace with a ValueError and creates no job."""
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn)
        with pytest.raises(ValueError, match="publish_at"):
            cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                  target_handle="@tigfoundation", publish_at=bad_pa)
    assert cd.get_candidate(sa_conn, cid)["status"] == "kept"  # not flipped to scheduled
    assert cp.list_publish_jobs(sa_conn, "orgA") == []


def test_schedule_stores_canonical_publish_at_for_lexical_compare(sa_conn):
    """The stored ``publish_at`` is the canonical strict-Z string the worker compares lexically. A
    PAST canonical value releases on the next claim; a surrounding-whitespace variant is rejected
    outright (never stored raw to early-release), so the worker only ever compares well-formed Z
    strings -- regression-pinning the bind of the validated value, not the raw param."""
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn)
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@tigfoundation", publish_at=PAST)
    assert cp.get_publish_job(sa_conn, jid)["publish_at"] == PAST  # exact canonical value stored
    with immediate_txn(sa_conn):
        claimed = cp.claim_due_jobs(sa_conn, now=NOW)
    assert [j["id"] for j in claimed] == [jid]  # a real past instant releases, as expected


def test_schedule_rejects_unbound_candidate(sa_conn):
    """SEC-3 / Phase 4: a candidate whose STORED target_handle is NULL is an unbound full-ops-only
    draft that cannot graduate to publish -- supplying a non-empty handle param must NOT let it be
    scheduled (the candidate's own binding is authoritative, not an arbitrary param)."""
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        # target_handle defaults to NULL -> an unbound draft.
        cid = cd.upsert_candidate(sa_conn, org_id="orgA", kind="meme",
                                  payload_json="{}", source="seed")
        assert cd.set_candidate_status(sa_conn, candidate_id=cid, org_id="orgA",
                                       status="kept", expected_status="pending")
        job = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@anything", publish_at=FUTURE)
    assert job is None                                            # cannot graduate to publish
    assert cd.get_candidate(sa_conn, cid)["status"] == "kept"     # untouched, still re-bindable
    assert cp.list_publish_jobs(sa_conn, "orgA") == []            # no job created


def test_schedule_rejects_handle_mismatch(sa_conn):
    """The caller-supplied target_handle MUST match the candidate's OWN stored binding -- a
    mismatch FAILS CLOSED (None, no job, candidate untouched), so a caller can never authorize one
    account but schedule the job as another (Phase 4 per-account re-check)."""
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, target="@tigfoundation")
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@someoneelse", publish_at=FUTURE)
    assert jid is None                                            # mismatch -> fail closed
    assert cd.get_candidate(sa_conn, cid)["status"] == "kept"     # candidate untouched
    assert cp.list_publish_jobs(sa_conn, "orgA") == []            # no job created


def test_schedule_matches_handle_case_insensitively(sa_conn):
    """The handle match is normalized (strip/@/casefold), so a differently-cased authorized handle
    is accepted and the job binds to the candidate's stored handle."""
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, target="@tigfoundation")
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="TIGFoundation", publish_at=FUTURE)
    assert jid and jid > 0
    assert cp.get_publish_job(sa_conn, jid)["target_handle"] == "@tigfoundation"  # stored binding


def test_claim_due_respects_publish_at(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        c_due = _kept(sa_conn, target="@a")
        c_future = _kept(sa_conn, target="@b")
        j_due = cp.schedule_candidate(sa_conn, candidate_id=c_due, org_id="orgA",
                                      target_handle="@a", publish_at=PAST)
        cp.schedule_candidate(sa_conn, candidate_id=c_future, org_id="orgA",
                              target_handle="@b", publish_at=FUTURE)
    with immediate_txn(sa_conn):
        claimed = cp.claim_due_jobs(sa_conn, now=NOW)
    ids = [j["id"] for j in claimed]
    assert ids == [j_due]                                  # only the past-publish_at job
    assert claimed[0]["release_state"] == "due"
    assert cp.get_publish_job(sa_conn, j_due)["release_state"] == "due"


def test_claim_due_respects_future_next_attempt_at(sa_conn):
    """RETRY GATE (masterplan future-gating): a job whose ``publish_at`` is in the past but whose
    ``next_attempt_at`` (a backoff timestamp) is in the FUTURE is NOT yet claimable -- the worker
    must honour the retry/backoff schedule, not release early. A past (or NULL) next_attempt_at is
    claimable as before."""
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        c_backoff = _kept(sa_conn, target="@a")
        c_ready = _kept(sa_conn, target="@b")
        j_backoff = cp.schedule_candidate(sa_conn, candidate_id=c_backoff, org_id="orgA",
                                          target_handle="@a", publish_at=PAST)
        j_ready = cp.schedule_candidate(sa_conn, candidate_id=c_ready, org_id="orgA",
                                        target_handle="@b", publish_at=PAST)
        # j_backoff has a FUTURE retry timestamp -> still gated despite a past publish_at.
        sa_conn.execute(
            text("UPDATE content_publish_jobs SET next_attempt_at = :f WHERE id = :id"),
            {"f": FUTURE, "id": j_backoff},
        )
    with immediate_txn(sa_conn):
        claimed = cp.claim_due_jobs(sa_conn, now=NOW)
    assert [j["id"] for j in claimed] == [j_ready]                         # only the un-gated job
    assert cp.get_publish_job(sa_conn, j_backoff)["release_state"] == "scheduled"  # still scheduled
    # Once the backoff window has passed (next_attempt_at <= now) it becomes claimable.
    with immediate_txn(sa_conn):
        sa_conn.execute(
            text("UPDATE content_publish_jobs SET next_attempt_at = :p WHERE id = :id"),
            {"p": PAST, "id": j_backoff},
        )
    with immediate_txn(sa_conn):
        claimed2 = cp.claim_due_jobs(sa_conn, now=NOW)
    assert [j["id"] for j in claimed2] == [j_backoff]


def test_claim_due_single_flight(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, target="@a")
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=PAST)
    with immediate_txn(sa_conn):
        first = cp.claim_due_jobs(sa_conn, now=NOW)
    with immediate_txn(sa_conn):
        second = cp.claim_due_jobs(sa_conn, now=NOW)   # already 'due' -> not re-claimed
    assert [j["id"] for j in first] == [jid] and second == []


def test_stale_scheduled_candidate_still_releases(sa_conn):
    """Masterplan-required: a SCHEDULED candidate whose ORIGINAL expires_at has passed STILL
    becomes due at publish_at (expire_due_candidates is pending-only -> no auto-expire)."""
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, target="@a", expires_at=PAST)   # already past its expiry
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=PAST)
    with immediate_txn(sa_conn):
        claimed = cp.claim_due_jobs(sa_conn, now=NOW)
    assert [j["id"] for j in claimed] == [jid]   # released despite the stale expires_at


def test_since_rejected_candidate_is_canceled_not_released(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, target="@a")
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=PAST)
        # operator rejects the candidate AFTER scheduling
        cd.set_candidate_status(sa_conn, candidate_id=cid, org_id="orgA",
                                status="rejected", expected_status="scheduled")
    with immediate_txn(sa_conn):
        claimed = cp.claim_due_jobs(sa_conn, now=NOW)
    assert claimed == []                                          # not released
    assert cp.get_publish_job(sa_conn, jid)["release_state"] == "canceled"


def test_hand_off_then_post_flips_candidate(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, target="@a")
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=PAST)
    with immediate_txn(sa_conn):
        cp.claim_due_jobs(sa_conn, now=NOW)                       # -> due
    with immediate_txn(sa_conn):
        assert cp.mark_handed_off(sa_conn, job_id=jid, org_id="orgA", authorized_target_handle="@a")
    assert cp.get_publish_job(sa_conn, jid)["release_state"] == "handed_off"
    with immediate_txn(sa_conn):
        assert cp.mark_posted(sa_conn, job_id=jid, org_id="orgA", authorized_target_handle="@a",
                              posted_ref="https://x.com/p/status/1899000000000000001")
    job = cp.get_publish_job(sa_conn, jid)
    # mark_posted now NORMALIZES the posted_ref to a bare tweet id (the JOIN key for outcome tracking)
    assert job["release_state"] == "posted" and job["posted_ref"] == "1899000000000000001"
    assert cd.get_candidate(sa_conn, cid)["status"] == "posted"   # candidate flipped


def test_cancel_returns_candidate_to_kept(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, target="@a")
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=FUTURE)
    with immediate_txn(sa_conn):
        assert cp.cancel_publish_job(sa_conn, job_id=jid, org_id="orgA", authorized_target_handle="@a")
    assert cp.get_publish_job(sa_conn, jid)["release_state"] == "canceled"
    assert cd.get_candidate(sa_conn, cid)["status"] == "kept"     # re-schedulable


def test_cancel_fails_closed_on_unauthorized_handle(sa_conn):
    """Phase-4 per-account re-check on CANCEL (parity with hand-off/post): even in the RIGHT org, a
    cancel must carry the job's OWN bound target_handle. A caller authorizing a DIFFERENT handle (or
    None) is refused -- it cannot revert another operator's scheduled job (candidate->kept,
    re-deck'd) with just job_id + org_id. The correctly-authorized handle still cancels."""
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, target="@a")
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=FUTURE)
    with immediate_txn(sa_conn):
        # wrong authorized handle -> fail closed (job untouched, candidate stays scheduled)
        assert cp.cancel_publish_job(sa_conn, job_id=jid, org_id="orgA",
                                     authorized_target_handle="@other") is False
        # a None authorized handle is likewise refused
        assert cp.cancel_publish_job(sa_conn, job_id=jid, org_id="orgA",
                                     authorized_target_handle=None) is False
    assert cp.get_publish_job(sa_conn, jid)["release_state"] == "scheduled"  # not canceled
    assert cd.get_candidate(sa_conn, cid)["status"] == "scheduled"           # not reverted to kept
    # the correctly-authorized handle still drives the cancel (matched, case-insensitive)
    with immediate_txn(sa_conn):
        assert cp.cancel_publish_job(sa_conn, job_id=jid, org_id="orgA",
                                     authorized_target_handle="A") is True
    assert cp.get_publish_job(sa_conn, jid)["release_state"] == "canceled"
    assert cd.get_candidate(sa_conn, cid)["status"] == "kept"     # re-schedulable


def test_state_flips_are_org_scoped(sa_conn):
    _seed(sa_conn, "orgA", "orgB"); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, org="orgA", target="@a")
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=PAST)
        cp.claim_due_jobs(sa_conn, now=NOW)
    with immediate_txn(sa_conn):
        # orgB cannot touch orgA's job (org check fail-closes before the handle check)
        assert cp.mark_handed_off(sa_conn, job_id=jid, org_id="orgB", authorized_target_handle="@a") is False
        assert cp.mark_posted(sa_conn, job_id=jid, org_id="orgB", authorized_target_handle="@a") is False
        assert cp.cancel_publish_job(sa_conn, job_id=jid, org_id="orgB", authorized_target_handle="@a") is False
    assert cp.get_publish_job(sa_conn, jid)["release_state"] == "due"  # untouched


def test_transitions_fail_closed_on_unauthorized_handle(sa_conn):
    """Phase-4 per-account re-check: even in the RIGHT org, a hand-off/post must carry the job's
    OWN bound target_handle. A caller authorizing a DIFFERENT handle (e.g. scoped to @other while
    the job is bound to @a) is refused -- it cannot drive the flip with just job_id + org_id."""
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, target="@a")
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=PAST)
        cp.claim_due_jobs(sa_conn, now=NOW)                       # job -> due
    with immediate_txn(sa_conn):
        # wrong authorized handle -> fail closed (no hand-off, no post)
        assert cp.mark_handed_off(sa_conn, job_id=jid, org_id="orgA",
                                  authorized_target_handle="@other") is False
        assert cp.mark_posted(sa_conn, job_id=jid, org_id="orgA",
                              authorized_target_handle="@other") is False
        # a None authorized handle is likewise refused
        assert cp.mark_posted(sa_conn, job_id=jid, org_id="orgA",
                              authorized_target_handle=None) is False
    assert cp.get_publish_job(sa_conn, jid)["release_state"] == "due"   # untouched
    # the correctly-authorized handle still drives the hand-off (matched, case-insensitive)
    with immediate_txn(sa_conn):
        assert cp.mark_handed_off(sa_conn, job_id=jid, org_id="orgA",
                                  authorized_target_handle="A") is True
    assert cp.get_publish_job(sa_conn, jid)["release_state"] == "handed_off"


def test_claim_due_cancels_job_whose_candidate_left_scheduled(sa_conn):
    """LIVENESS GATE (hardened): a scheduled job whose candidate is no longer EXACTLY 'scheduled' --
    e.g. a stale-tab/forged swipe flipped it back to 'kept' -- must NOT be released. The worker
    cancels the job instead of flipping it to 'due' (else a publish job comes due for a candidate
    that isn't scheduled, breaking hand-off/post)."""
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, target="@a")
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=PAST)
        # candidate clobbered back to 'kept' AFTER scheduling (the decide-route lifecycle bug class)
        assert cd.set_candidate_status(sa_conn, candidate_id=cid, org_id="orgA",
                                       status="kept", expected_status="scheduled")
    with immediate_txn(sa_conn):
        claimed = cp.claim_due_jobs(sa_conn, now=NOW)
    assert claimed == []                                                # NOT released
    assert cp.get_publish_job(sa_conn, jid)["release_state"] == "canceled"  # cancelled, not 'due'
    assert cd.get_candidate(sa_conn, cid)["status"] == "kept"           # candidate left as-is


def test_list_publish_jobs_carries_candidate_draft_and_media(sa_conn):
    """The calendar feed JOINs the candidate so a 'due' job can surface its draft/caption +
    rendered-media ref for the operator hand-off (composeUrl + media download)."""
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = cd.upsert_candidate(
            sa_conn, org_id="orgA", kind="meme",
            payload_json='{"text":"gm from the deck"}', source="seed",
            target_handle="@a", media_content_id="sable-tig/memes/abc.png",
        )
        assert cd.set_candidate_status(sa_conn, candidate_id=cid, org_id="orgA",
                                       status="kept", expected_status="pending")
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=FUTURE)
    jobs = cp.list_publish_jobs(sa_conn, "orgA")
    assert len(jobs) == 1 and jobs[0]["id"] == jid
    assert jobs[0]["candidate_payload_json"] == '{"text":"gm from the deck"}'
    assert jobs[0]["candidate_media_content_id"] == "sable-tig/memes/abc.png"
    # the original job columns still ride along unchanged
    assert jobs[0]["target_handle"] == "@a" and jobs[0]["release_state"] == "scheduled"


def test_list_publish_jobs_is_org_scoped_and_ordered(sa_conn):
    _seed(sa_conn, "orgA", "orgB"); sa_conn.commit()
    with immediate_txn(sa_conn):
        a1 = _kept(sa_conn, org="orgA", target="@a1")
        a2 = _kept(sa_conn, org="orgA", target="@a2")
        b1 = _kept(sa_conn, org="orgB", target="@b1")
        j2 = cp.schedule_candidate(sa_conn, candidate_id=a2, org_id="orgA",
                                   target_handle="@a2", publish_at=FUTURE)
        j1 = cp.schedule_candidate(sa_conn, candidate_id=a1, org_id="orgA",
                                   target_handle="@a1", publish_at=PAST)
        cp.schedule_candidate(sa_conn, candidate_id=b1, org_id="orgB",
                              target_handle="@b1", publish_at=PAST)
    jobs = cp.list_publish_jobs(sa_conn, "orgA")
    assert [j["id"] for j in jobs] == [j1, j2]   # soonest publish_at first, orgB excluded
    assert cp.list_publish_jobs(sa_conn, "orgA", states=()) == []


def test_post_and_handoff_refuse_since_rejected_after_claim(sa_conn):
    """M1: a candidate rejected AFTER its job is claimed ('due') must NOT be handed-off or posted
    (the claim-due cancel only covers the still-'scheduled' window) -- no job=posted/candidate=rejected
    split."""
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, target="@a")
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=PAST)
        cp.claim_due_jobs(sa_conn, now=NOW)                                  # job -> due
        cd.set_candidate_status(sa_conn, candidate_id=cid, org_id="orgA",
                                status="rejected", expected_status="scheduled")  # rejected AFTER claim
    with immediate_txn(sa_conn):
        assert cp.mark_handed_off(sa_conn, job_id=jid, org_id="orgA", authorized_target_handle="@a") is False
        assert cp.mark_posted(sa_conn, job_id=jid, org_id="orgA", authorized_target_handle="@a") is False
    assert cp.get_publish_job(sa_conn, jid)["release_state"] == "due"        # job not advanced
    assert cd.get_candidate(sa_conn, cid)["status"] == "rejected"            # candidate not un-rejected


def test_mark_handed_off_only_from_due(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, target="@a")
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=FUTURE)   # still 'scheduled'
    with immediate_txn(sa_conn):
        assert cp.mark_handed_off(sa_conn, job_id=jid, org_id="orgA",
                                  authorized_target_handle="@a") is False  # not 'due' yet
    assert cp.get_publish_job(sa_conn, jid)["release_state"] == "scheduled"


def test_double_post_is_idempotent(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, target="@a")
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=PAST)
        cp.claim_due_jobs(sa_conn, now=NOW)
    with immediate_txn(sa_conn):
        assert cp.mark_posted(sa_conn, job_id=jid, org_id="orgA", authorized_target_handle="@a") is True
    with immediate_txn(sa_conn):
        assert cp.mark_posted(sa_conn, job_id=jid, org_id="orgA",
                              authorized_target_handle="@a") is False   # already posted
    assert cd.get_candidate(sa_conn, cid)["status"] == "posted"


def test_cancel_of_posted_job_refused_and_candidate_unchanged(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, target="@a")
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=PAST)
        cp.claim_due_jobs(sa_conn, now=NOW)
        cp.mark_posted(sa_conn, job_id=jid, org_id="orgA", authorized_target_handle="@a")
    with immediate_txn(sa_conn):
        assert cp.cancel_publish_job(sa_conn, job_id=jid, org_id="orgA",
                                     authorized_target_handle="@a") is False  # can't cancel posted
    assert cp.get_publish_job(sa_conn, jid)["release_state"] == "posted"
    assert cd.get_candidate(sa_conn, cid)["status"] == "posted"   # NOT reverted to kept


# === SQL-path (raw 077_*.sql) ===============================================

def _raw():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_schema(conn)
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('o','o')")
    conn.execute(
        "INSERT INTO content_candidates (org_id, kind, status, payload_json, source) "
        "VALUES ('o','meme','kept','{}','s')"
    )
    return conn, conn.execute("SELECT id FROM content_candidates").fetchone()["id"]


def test_sql_release_state_check_rejects_bad_value():
    conn, cid = _raw()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO content_publish_jobs (candidate_id, org_id, target_handle, release_state, publish_at) "
            "VALUES (?, 'o', '@h', 'bogus', '2020-01-01T00:00:00Z')",
            (cid,),
        )
    conn.close()


def test_sql_candidate_delete_cascades_jobs():
    conn, cid = _raw()
    conn.execute(
        "INSERT INTO content_publish_jobs (candidate_id, org_id, target_handle, release_state, publish_at) "
        "VALUES (?, 'o', '@h', 'scheduled', '2020-01-01T00:00:00Z')",
        (cid,),
    )
    assert conn.execute("SELECT count(*) FROM content_publish_jobs").fetchone()[0] == 1
    conn.execute("DELETE FROM content_candidates WHERE id = ?", (cid,))
    assert conn.execute("SELECT count(*) FROM content_publish_jobs").fetchone()[0] == 0  # cascaded
    conn.close()
