"""Shared fixtures for all tests."""
from __future__ import annotations

import os
import sqlite3

import pytest
from sqlalchemy import create_engine, event, text

from sable_platform.db.schema import metadata as sa_metadata

# Ensure SABLE_OPERATOR_ID is set so CLI tests don't see the "unknown" warning.
os.environ.setdefault("SABLE_OPERATOR_ID", "test")

from sable_platform.db.connection import ensure_schema


# ---------------------------------------------------------------------------
# Legacy fixtures (raw sqlite3 — used by all existing tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def in_memory_db() -> sqlite3.Connection:
    """Return a fully migrated in-memory SQLite connection."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


@pytest.fixture
def org_db(in_memory_db) -> tuple[sqlite3.Connection, str]:
    """Return (conn, org_id) with a test org inserted."""
    org_id = "test_org_001"
    in_memory_db.execute(
        "INSERT INTO orgs (org_id, display_name) VALUES (?, ?)",
        (org_id, "Test Org"),
    )
    in_memory_db.commit()
    return in_memory_db, org_id


# ---------------------------------------------------------------------------
# SQLAlchemy fixtures (new — opt-in for migrated modules)
# ---------------------------------------------------------------------------


@pytest.fixture
def sa_engine():
    """SQLAlchemy engine backed by in-memory SQLite with full schema."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, connection_record):  # noqa: ARG001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    sa_metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def sa_conn(sa_engine):
    """SQLAlchemy connection from in-memory engine."""
    with sa_engine.connect() as conn:
        yield conn


@pytest.fixture
def sa_org(sa_conn):
    """Return (sa_conn, org_id) with a test org inserted."""
    org_id = "test_org_001"
    sa_conn.execute(
        text("INSERT INTO orgs (org_id, display_name) VALUES (:org_id, :name)"),
        {"org_id": org_id, "name": "Test Org"},
    )
    sa_conn.commit()
    return sa_conn, org_id
