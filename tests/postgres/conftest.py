"""Fixtures for live PostgreSQL integration tests.

These tests are skipped unless ``SABLE_TEST_POSTGRES_URL`` is set.
The URL should point at an admin-accessible database on a disposable
Postgres instance (the GitHub Actions service uses ``/postgres``).
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL, make_url

from sable_platform.db.compat_conn import CompatConnection
from sable_platform.db.migrate_pg import _run_alembic_upgrade
from sable_platform.db.schema import metadata as sa_metadata


def _postgres_base_url() -> URL:
    raw_url = os.environ.get("SABLE_TEST_POSTGRES_URL", "")
    if not raw_url:
        pytest.skip("SABLE_TEST_POSTGRES_URL not set; skipping live Postgres tests")
    pytest.importorskip("psycopg2")
    return make_url(raw_url)


def _create_sqlite_source_engine():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, connection_record):  # noqa: ARG001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    sa_metadata.create_all(engine)
    return engine


def _render_url(url: URL) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def sqlite_source_engine():
    """SQLite source DB mirroring the current schema for migration tests."""
    engine = _create_sqlite_source_engine()
    yield engine
    engine.dispose()


@pytest.fixture
def postgres_db_url():
    """Create and drop an isolated PostgreSQL database for one test."""
    base_url = _postgres_base_url()
    admin_db = base_url.database or "postgres"
    admin_url = URL.create(
        drivername=base_url.drivername,
        username=base_url.username,
        password=base_url.password,
        host=base_url.host,
        port=base_url.port,
        database=admin_db,
        query=base_url.query,
    )
    db_name = f"sable_test_{uuid.uuid4().hex}"
    db_url = URL.create(
        drivername=base_url.drivername,
        username=base_url.username,
        password=base_url.password,
        host=base_url.host,
        port=base_url.port,
        database=db_name,
        query=base_url.query,
    )
    admin_engine = create_engine(_render_url(admin_url), isolation_level="AUTOCOMMIT")

    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        yield _render_url(db_url)
    finally:
        with admin_engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid)"
                    " FROM pg_stat_activity"
                    " WHERE datname=:db_name AND pid <> pg_backend_pid()"
                ),
                {"db_name": db_name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin_engine.dispose()


@pytest.fixture
def postgres_engine(postgres_db_url):
    """Live PostgreSQL engine with Alembic-applied schema."""
    _run_alembic_upgrade(postgres_db_url)
    engine = create_engine(postgres_db_url)
    yield engine
    engine.dispose()


@pytest.fixture
def postgres_conn(postgres_engine):
    """CompatConnection backed by a live PostgreSQL database."""
    sa_conn = postgres_engine.connect()
    conn = CompatConnection(sa_conn)
    yield conn
    sa_conn.close()


@pytest.fixture
def postgres_wf_db(postgres_conn):
    """Workflow-style Postgres DB with a seed org."""
    postgres_conn.execute(
        "INSERT INTO orgs (org_id, display_name) VALUES (?, ?)",
        ("wf_org", "WF Test Org"),
    )
    postgres_conn.commit()
    return postgres_conn
