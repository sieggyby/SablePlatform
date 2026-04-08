"""Shared fixtures for all tests."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, event, text

from sable_platform.db.compat_conn import CompatConnection
from sable_platform.db.schema import metadata as sa_metadata

# Ensure SABLE_OPERATOR_ID is set so CLI tests don't see the "unknown" warning.
os.environ.setdefault("SABLE_OPERATOR_ID", "test")


# ---------------------------------------------------------------------------
# Shared SA engine (used by both compat and native fixtures)
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


# ---------------------------------------------------------------------------
# Compat fixtures (drop-in replacement for legacy sqlite3 fixtures)
#
# These return CompatConnection which supports both ? positional params
# and row["col"] dict access, so all existing tests work unchanged.
# ---------------------------------------------------------------------------


@pytest.fixture
def in_memory_db(sa_engine):
    """Return a CompatConnection with in-memory SQLite + full schema."""
    with sa_engine.connect() as sa_conn:
        yield CompatConnection(sa_conn)


@pytest.fixture
def org_db(in_memory_db):
    """Return (compat_conn, org_id) with a test org inserted."""
    org_id = "test_org_001"
    in_memory_db.execute(
        "INSERT INTO orgs (org_id, display_name) VALUES (?, ?)",
        (org_id, "Test Org"),
    )
    in_memory_db.commit()
    return in_memory_db, org_id


# ---------------------------------------------------------------------------
# Native SQLAlchemy fixtures (for modules already converted to SA text())
# ---------------------------------------------------------------------------


@pytest.fixture
def sa_conn(sa_engine):
    """Raw SQLAlchemy connection from in-memory engine."""
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
