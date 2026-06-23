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


def test_claim_due_single_flight(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn)
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
        cid = _kept(sa_conn, expires_at=PAST)   # already past its expiry
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=PAST)
    with immediate_txn(sa_conn):
        claimed = cp.claim_due_jobs(sa_conn, now=NOW)
    assert [j["id"] for j in claimed] == [jid]   # released despite the stale expires_at


def test_since_rejected_candidate_is_canceled_not_released(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn)
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
        cid = _kept(sa_conn)
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=PAST)
    with immediate_txn(sa_conn):
        cp.claim_due_jobs(sa_conn, now=NOW)                       # -> due
    with immediate_txn(sa_conn):
        assert cp.mark_handed_off(sa_conn, job_id=jid, org_id="orgA")
    assert cp.get_publish_job(sa_conn, jid)["release_state"] == "handed_off"
    with immediate_txn(sa_conn):
        assert cp.mark_posted(sa_conn, job_id=jid, org_id="orgA", posted_ref="https://x.com/p/1")
    job = cp.get_publish_job(sa_conn, jid)
    assert job["release_state"] == "posted" and job["posted_ref"] == "https://x.com/p/1"
    assert cd.get_candidate(sa_conn, cid)["status"] == "posted"   # candidate flipped


def test_cancel_returns_candidate_to_kept(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn)
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=FUTURE)
    with immediate_txn(sa_conn):
        assert cp.cancel_publish_job(sa_conn, job_id=jid, org_id="orgA")
    assert cp.get_publish_job(sa_conn, jid)["release_state"] == "canceled"
    assert cd.get_candidate(sa_conn, cid)["status"] == "kept"     # re-schedulable


def test_state_flips_are_org_scoped(sa_conn):
    _seed(sa_conn, "orgA", "orgB"); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _kept(sa_conn, org="orgA")
        jid = cp.schedule_candidate(sa_conn, candidate_id=cid, org_id="orgA",
                                    target_handle="@a", publish_at=PAST)
        cp.claim_due_jobs(sa_conn, now=NOW)
    with immediate_txn(sa_conn):
        # orgB cannot touch orgA's job
        assert cp.mark_handed_off(sa_conn, job_id=jid, org_id="orgB") is False
        assert cp.mark_posted(sa_conn, job_id=jid, org_id="orgB") is False
        assert cp.cancel_publish_job(sa_conn, job_id=jid, org_id="orgB") is False
    assert cp.get_publish_job(sa_conn, jid)["release_state"] == "due"  # untouched


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
