"""Migration 040 parity + nullability assertions.

Verifies the three additions land correctly on both drivers:
  - kol_create_audit table (email NULLABLE so anonymous failures can log)
  - jobs.worker_id (nullable)
  - job_steps.next_retry_at (nullable)

Postgres assertions only run when SABLE_TEST_POSTGRES_URL is set (matches
the existing live-Postgres test convention in this repo).
"""
from __future__ import annotations

import os
import sqlite3

import pytest
from sqlalchemy import create_engine, inspect

from sable_platform.db.connection import ensure_schema


def _legacy_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# SQLite (legacy migration runner) parity
# ---------------------------------------------------------------------------


def test_schema_version_is_40():
    conn = _legacy_conn()
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row["version"] >= 40


def test_kol_create_audit_table_exists():
    conn = _legacy_conn()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='kol_create_audit'"
    ).fetchall()
    assert len(rows) == 1


def test_kol_create_audit_columns():
    conn = _legacy_conn()
    cols = {row[1]: row for row in conn.execute(
        "PRAGMA table_info(kol_create_audit)"
    ).fetchall()}
    expected = {
        "id", "at_utc", "email", "endpoint", "method",
        "outcome", "job_id", "ip", "user_agent",
    }
    assert expected <= set(cols.keys()), f"missing: {expected - set(cols.keys())}"


def test_kol_create_audit_email_nullable_sqlite():
    """Codex round-2 critical #5: email MUST be nullable on SQLite."""
    conn = _legacy_conn()
    cols = {row[1]: row for row in conn.execute(
        "PRAGMA table_info(kol_create_audit)"
    ).fetchall()}
    # PRAGMA table_info column 3 = notnull (1 if NOT NULL, 0 otherwise)
    assert cols["email"][3] == 0, "email column must be NULLABLE on SQLite"


def test_jobs_worker_id_column_nullable_sqlite():
    conn = _legacy_conn()
    cols = {row[1]: row for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "worker_id" in cols, "jobs.worker_id missing"
    assert cols["worker_id"][3] == 0, "jobs.worker_id must be NULLABLE"


def test_job_steps_next_retry_at_column_nullable_sqlite():
    conn = _legacy_conn()
    cols = {row[1]: row for row in conn.execute("PRAGMA table_info(job_steps)").fetchall()}
    assert "next_retry_at" in cols, "job_steps.next_retry_at missing"
    assert cols["next_retry_at"][3] == 0, "job_steps.next_retry_at must be NULLABLE"


def test_indexes_exist_sqlite():
    conn = _legacy_conn()
    rows_audit = conn.execute("PRAGMA index_list(kol_create_audit)").fetchall()
    audit_idx = {r[1] for r in rows_audit}
    for expected in (
        "idx_kol_create_audit_email",
        "idx_kol_create_audit_at",
        "idx_kol_create_audit_outcome",
    ):
        assert expected in audit_idx, f"audit index missing: {expected}"

    rows_jobs = conn.execute("PRAGMA index_list(jobs)").fetchall()
    assert "idx_jobs_worker" in {r[1] for r in rows_jobs}

    rows_steps = conn.execute("PRAGMA index_list(job_steps)").fetchall()
    assert "idx_job_steps_next_retry" in {r[1] for r in rows_steps}


# ---------------------------------------------------------------------------
# SA metadata parity (mirrors test_schema.py approach but scoped to mig 040)
# ---------------------------------------------------------------------------


def test_sa_path_creates_kol_create_audit_with_nullable_email():
    """SQLAlchemy metadata.create_all() must yield the same nullable email."""
    from sable_platform.db.schema import metadata

    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    inspector = inspect(engine)
    cols = {c["name"]: c for c in inspector.get_columns("kol_create_audit")}
    assert cols["email"]["nullable"] is True
    assert cols["email"]["name"] == "email"
    engine.dispose()


def test_sa_path_jobs_worker_id_nullable():
    from sable_platform.db.schema import metadata

    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    inspector = inspect(engine)
    cols = {c["name"]: c for c in inspector.get_columns("jobs")}
    assert "worker_id" in cols
    assert cols["worker_id"]["nullable"] is True
    engine.dispose()


def test_sa_path_job_steps_next_retry_at_nullable():
    from sable_platform.db.schema import metadata

    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    inspector = inspect(engine)
    cols = {c["name"]: c for c in inspector.get_columns("job_steps")}
    assert "next_retry_at" in cols
    assert cols["next_retry_at"]["nullable"] is True
    engine.dispose()


# ---------------------------------------------------------------------------
# Live-Postgres parity (skipped unless SABLE_TEST_POSTGRES_URL is set)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("SABLE_TEST_POSTGRES_URL"),
    reason="SABLE_TEST_POSTGRES_URL not set",
)
def test_postgres_alembic_path_email_nullable():
    """When a live Postgres URL is available, run alembic upgrade head and
    assert the same nullable-email constraint holds on the Postgres side."""
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", os.environ["SABLE_TEST_POSTGRES_URL"])
    command.upgrade(cfg, "head")

    engine = create_engine(os.environ["SABLE_TEST_POSTGRES_URL"])
    inspector = inspect(engine)
    cols = {c["name"]: c for c in inspector.get_columns("kol_create_audit")}
    assert cols["email"]["nullable"] is True
    jobs_cols = {c["name"]: c for c in inspector.get_columns("jobs")}
    assert jobs_cols["worker_id"]["nullable"] is True
    steps_cols = {c["name"]: c for c in inspector.get_columns("job_steps")}
    assert steps_cols["next_retry_at"]["nullable"] is True
    engine.dispose()
