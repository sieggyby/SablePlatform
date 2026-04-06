"""Shared fixtures for all tests."""
from __future__ import annotations

import os
import sqlite3

import pytest

# Ensure SABLE_OPERATOR_ID is set so CLI tests don't see the "unknown" warning.
os.environ.setdefault("SABLE_OPERATOR_ID", "test")

from sable_platform.db.connection import ensure_schema


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
