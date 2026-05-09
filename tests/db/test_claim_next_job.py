"""Tests for claim_next_job and the lifecycle helpers added in Phase C of
the any-project KOL wizard build (see SableKOL/docs/any_project_wizard_plan.md).

Coverage:
    * single-claim / completion / no-second-claim
    * wrong job_type returns None
    * stale-reclaim (updated_at older than cutoff → re-claimable)
    * two-racer race test: 50 iterations, exactly one worker wins each
    * complete_job, fail_job, release_job, defer_step state transitions
    * Postgres parity (skipped unless SABLE_TEST_POSTGRES_URL is set)
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection

from sable_platform.db.compat_conn import CompatConnection
from sable_platform.db.connection import ensure_schema
from sable_platform.db.jobs import (
    add_step,
    claim_next_job,
    complete_job,
    complete_step,
    create_job,
    defer_step,
    fail_job,
    release_job,
)


# ---------------------------------------------------------------------------
# Single-connection tests (in-memory SQLite via shared conftest fixture)
# ---------------------------------------------------------------------------


def test_claim_returns_pending_job(org_db):
    conn, org_id = org_db
    jid = create_job(conn, org_id, "kol_create", config={"handle": "solstitch"})
    claimed = claim_next_job(conn, "kol_create", "worker-1")
    assert claimed is not None
    assert claimed["job_id"] == jid
    assert claimed["config_json"] == {"handle": "solstitch"}
    assert claimed["org_id"] == org_id


def test_claim_marks_job_running_and_stamps_worker(org_db):
    conn, org_id = org_db
    create_job(conn, org_id, "kol_create")
    claim_next_job(conn, "kol_create", "worker-abc")
    row = conn.execute(
        "SELECT status, worker_id FROM jobs WHERE job_type='kol_create'"
    ).fetchone()
    assert row["status"] == "running"
    assert row["worker_id"] == "worker-abc"


def test_claim_returns_none_when_no_pending(org_db):
    conn, _ = org_db
    assert claim_next_job(conn, "kol_create", "worker-1") is None


def test_claim_returns_none_for_other_job_type(org_db):
    conn, org_id = org_db
    create_job(conn, org_id, "diagnostic")
    assert claim_next_job(conn, "kol_create", "worker-1") is None


def test_claim_is_idempotent_after_completion(org_db):
    conn, org_id = org_db
    create_job(conn, org_id, "kol_create")
    first = claim_next_job(conn, "kol_create", "worker-1")
    assert first is not None
    complete_job(conn, first["job_id"])
    second = claim_next_job(conn, "kol_create", "worker-1")
    assert second is None


def test_claim_orders_by_created_at(org_db):
    conn, org_id = org_db
    older = create_job(conn, org_id, "kol_create")
    # Force a distinct created_at on the second job so ORDER BY is deterministic.
    conn.execute(
        "UPDATE jobs SET created_at=datetime('now', '-5 minutes') WHERE job_id=?",
        (older,),
    )
    conn.commit()
    create_job(conn, org_id, "kol_create")
    claimed = claim_next_job(conn, "kol_create", "worker-1")
    assert claimed["job_id"] == older


def test_claim_skips_already_running_job_within_stale_window(org_db):
    conn, org_id = org_db
    create_job(conn, org_id, "kol_create")
    first = claim_next_job(conn, "kol_create", "worker-1")
    assert first is not None
    # Default stale window is 10 minutes — fresh running job should NOT be re-claimable.
    assert claim_next_job(conn, "kol_create", "worker-2") is None


def test_claim_reclaims_stale_running_job(org_db):
    conn, org_id = org_db
    create_job(conn, org_id, "kol_create")
    first = claim_next_job(conn, "kol_create", "worker-1")
    assert first is not None
    # Simulate 11-minute-old running job (worker-1 crashed).
    conn.execute(
        "UPDATE jobs SET updated_at=datetime('now', '-11 minutes') WHERE job_id=?",
        (first["job_id"],),
    )
    conn.commit()
    second = claim_next_job(conn, "kol_create", "worker-2")
    assert second is not None
    assert second["job_id"] == first["job_id"]
    row = conn.execute(
        "SELECT worker_id FROM jobs WHERE job_id=?", (first["job_id"],)
    ).fetchone()
    assert row["worker_id"] == "worker-2"


def test_complete_job_sets_status_and_result(org_db):
    conn, org_id = org_db
    jid = create_job(conn, org_id, "kol_create")
    claim_next_job(conn, "kol_create", "w1")
    complete_job(conn, jid, result={"yaml_path": "/opt/sable/clients/x.yaml"})
    row = conn.execute("SELECT status, result_json, completed_at FROM jobs WHERE job_id=?", (jid,)).fetchone()
    assert row["status"] == "done"
    assert row["completed_at"] is not None
    import json
    assert json.loads(row["result_json"]) == {"yaml_path": "/opt/sable/clients/x.yaml"}


def test_fail_job_records_error(org_db):
    conn, org_id = org_db
    jid = create_job(conn, org_id, "kol_create")
    claim_next_job(conn, "kol_create", "w1")
    fail_job(conn, jid, error="enrich exhausted retries")
    row = conn.execute("SELECT status, error_message FROM jobs WHERE job_id=?", (jid,)).fetchone()
    assert row["status"] == "failed"
    assert row["error_message"] == "enrich exhausted retries"


def test_release_job_returns_to_pending(org_db):
    conn, org_id = org_db
    jid = create_job(conn, org_id, "kol_create")
    claim_next_job(conn, "kol_create", "w1")
    release_job(conn, jid)
    row = conn.execute("SELECT status, worker_id FROM jobs WHERE job_id=?", (jid,)).fetchone()
    assert row["status"] == "pending"
    assert row["worker_id"] is None
    # And it can be claimed again.
    second = claim_next_job(conn, "kol_create", "w2")
    assert second is not None
    assert second["job_id"] == jid


def test_defer_step_sets_next_retry_at(org_db):
    conn, org_id = org_db
    jid = create_job(conn, org_id, "kol_create")
    sid = add_step(conn, jid, "enrich")
    defer_step(conn, sid, "2026-12-31T00:00:00+00:00")
    row = conn.execute("SELECT next_retry_at FROM job_steps WHERE step_id=?", (sid,)).fetchone()
    assert row["next_retry_at"] == "2026-12-31T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Race test — file-backed SQLite, two threads, 50 iterations
# ---------------------------------------------------------------------------


def _open_file_compat_conn(db_path: str) -> CompatConnection:
    """Open a file-backed CompatConnection that can be used from a worker thread.

    Uses check_same_thread=False because the test passes the connection across
    threads (one per worker).  busy_timeout=5000 lets concurrent claimers wait
    for the database lock.
    """
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 5.0},
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, connection_record):  # noqa: ARG001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    sa_conn = engine.connect()
    return CompatConnection(sa_conn)


def test_two_racers_only_one_wins():
    """Run 50 iterations of "two workers race to claim the same single job".

    On every iteration:
      * insert one fresh kol_create job
      * launch two threads, each calling claim_next_job
      * exactly one MUST get the job, the other MUST get None

    File-backed SQLite + WAL + busy_timeout serializes the writes; the second
    racer's UPDATE finds the WHERE predicate no longer matches (status!='pending')
    so its RETURNING yields zero rows.
    """
    iterations = 50
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "race.db")

        # Bootstrap schema + an org via a one-shot raw sqlite3 connection
        # (the migration runner takes a raw sqlite3.Connection).
        raw = sqlite3.connect(db_path)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys=ON")
        ensure_schema(raw)
        raw.execute(
            "INSERT INTO orgs (org_id, display_name) VALUES (?, ?)",
            ("race_org", "Race Org"),
        )
        raw.commit()
        raw.close()

        wins = []
        losses = []

        def racer(idx: int, jid: str) -> None:
            conn = _open_file_compat_conn(db_path)
            try:
                # Force both racers to actually contend by blocking on a barrier.
                barrier.wait(timeout=5.0)
                claimed = claim_next_job(conn, "kol_create", f"worker-{idx}")
            finally:
                conn.close()
            if claimed is not None:
                wins.append((idx, claimed["job_id"]))
            else:
                losses.append((idx, jid))

        for i in range(iterations):
            # Insert a single fresh job.
            seed = _open_file_compat_conn(db_path)
            jid = create_job(seed, "race_org", "kol_create", config={"i": i})
            seed.close()

            barrier = threading.Barrier(2)
            t1 = threading.Thread(target=racer, args=(1, jid))
            t2 = threading.Thread(target=racer, args=(2, jid))
            t1.start()
            t2.start()
            t1.join(timeout=10.0)
            t2.join(timeout=10.0)

            iter_wins = [w for w in wins if w[1] == jid]
            iter_losses = [l for l in losses if l[1] == jid]
            assert len(iter_wins) == 1, (
                f"iter {i}: expected exactly one winner, got {len(iter_wins)} "
                f"(wins={iter_wins}, losses={iter_losses})"
            )
            assert len(iter_losses) == 1, (
                f"iter {i}: expected exactly one loser, got {len(iter_losses)}"
            )

            # Reset for next iteration.
            cleanup = _open_file_compat_conn(db_path)
            cleanup.execute("DELETE FROM job_steps WHERE job_id=?", (jid,))
            cleanup.execute("DELETE FROM jobs WHERE job_id=?", (jid,))
            cleanup.commit()
            cleanup.close()


# ---------------------------------------------------------------------------
# Postgres parity (skipped unless SABLE_TEST_POSTGRES_URL is set)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("SABLE_TEST_POSTGRES_URL"),
    reason="SABLE_TEST_POSTGRES_URL not set",
)
def test_postgres_claim_round_trip():
    """Smoke-check the Postgres path: FOR UPDATE SKIP LOCKED branch works."""
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", os.environ["SABLE_TEST_POSTGRES_URL"])
    command.upgrade(cfg, "head")

    engine = create_engine(os.environ["SABLE_TEST_POSTGRES_URL"])
    sa_conn = engine.connect()
    conn = CompatConnection(sa_conn)
    try:
        org_id = f"test_pg_org_{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO orgs (org_id, display_name) VALUES (?, ?)",
            (org_id, "PG Test Org"),
        )
        conn.commit()
        jid = create_job(conn, org_id, "kol_create", config={"x": 1})
        claimed = claim_next_job(conn, "kol_create", "pg-worker")
        assert claimed is not None
        assert claimed["job_id"] == jid
        complete_job(conn, jid)
        # Cleanup
        conn.execute("DELETE FROM jobs WHERE job_id=?", (jid,))
        conn.execute("DELETE FROM orgs WHERE org_id=?", (org_id,))
        conn.commit()
    finally:
        conn.close()
        engine.dispose()
