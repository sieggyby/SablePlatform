"""kol_create_audit insert-fixture tests (Migration 040).

Asserts the audit table accepts every documented outcome, that the FK to
jobs(job_id) is enforced when set, and that anonymous failures (no email)
can still write — Codex round-2 critical #5.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from sable_platform.db.connection import ensure_schema


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def _insert_org_and_job(conn: sqlite3.Connection) -> str:
    """Helper: create an org + job_type='kol_create' job and return job_id."""
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, status) "
        "VALUES ('test_org', 'Test', 'active')"
    )
    job_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO jobs (job_id, org_id, job_type, status, config_json) "
        "VALUES (?, 'test_org', 'kol_create', 'pending', '{}')",
        (job_id,),
    )
    conn.commit()
    return job_id


@pytest.mark.parametrize(
    "outcome",
    ["allowed", "denied", "quota_exceeded", "auth_failed"],
)
def test_all_documented_outcomes_insert_successfully(outcome: str):
    """Every outcome documented in the plan can be inserted."""
    conn = _conn()
    conn.execute(
        "INSERT INTO kol_create_audit "
        "(email, endpoint, method, outcome) "
        "VALUES (?, ?, ?, ?)",
        ("siegby@gmail.com", "/api/ops/kol-network/preflight", "POST", outcome),
    )
    conn.commit()
    row = conn.execute(
        "SELECT outcome, email FROM kol_create_audit WHERE outcome=?",
        (outcome,),
    ).fetchone()
    assert row["outcome"] == outcome


def test_email_null_accepted_for_auth_failed():
    """Codex round-2 critical #5: anonymous (no session) requests must still
    log to audit; this means email must accept NULL when outcome='auth_failed'.
    """
    conn = _conn()
    conn.execute(
        "INSERT INTO kol_create_audit "
        "(email, endpoint, method, outcome) "
        "VALUES (NULL, '/api/ops/kol-network/preflight', 'POST', 'auth_failed')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT email, outcome FROM kol_create_audit WHERE outcome='auth_failed'"
    ).fetchone()
    assert row["email"] is None


def test_job_id_null_accepted():
    """Most audit hits will have no associated job (preflight, denied)."""
    conn = _conn()
    conn.execute(
        "INSERT INTO kol_create_audit "
        "(email, endpoint, method, outcome, job_id) "
        "VALUES ('a@b.co', '/api/ops/kol-network/preflight', 'POST', 'allowed', NULL)"
    )
    conn.commit()
    row = conn.execute(
        "SELECT job_id FROM kol_create_audit WHERE email='a@b.co'"
    ).fetchone()
    assert row["job_id"] is None


def test_job_id_fk_enforced_when_set():
    """When job_id IS set, it must reference an existing jobs row."""
    conn = _conn()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO kol_create_audit "
            "(email, endpoint, method, outcome, job_id) "
            "VALUES ('a@b.co', '/api/ops/kol-network/create', 'POST', 'allowed', "
            "'00000000-0000-0000-0000-000000000000')"
        )
        conn.commit()


def test_job_id_fk_succeeds_with_real_job():
    """The whole point: cost_events / audit can FK to jobs(job_id) cleanly."""
    conn = _conn()
    job_id = _insert_org_and_job(conn)
    conn.execute(
        "INSERT INTO kol_create_audit "
        "(email, endpoint, method, outcome, job_id) "
        "VALUES ('a@b.co', '/api/ops/kol-network/create', 'POST', 'allowed', ?)",
        (job_id,),
    )
    conn.commit()
    row = conn.execute(
        "SELECT job_id FROM kol_create_audit WHERE email='a@b.co'"
    ).fetchone()
    assert row["job_id"] == job_id


def test_at_utc_default_populated():
    """at_utc has a default of datetime('now') so callers don't have to set it."""
    conn = _conn()
    conn.execute(
        "INSERT INTO kol_create_audit "
        "(email, endpoint, method, outcome) "
        "VALUES ('a@b.co', '/api/ops/kol-network/preflight', 'POST', 'allowed')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT at_utc FROM kol_create_audit WHERE email='a@b.co'"
    ).fetchone()
    assert row["at_utc"] is not None
    assert len(row["at_utc"]) > 0
