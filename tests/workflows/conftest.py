"""Workflow test fixtures."""
from __future__ import annotations

import sqlite3

import pytest

from sable_platform.db.connection import ensure_schema


@pytest.fixture
def wf_db() -> sqlite3.Connection:
    """In-memory DB with schema + a test org."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('wf_org', 'WF Test Org')")
    conn.commit()
    return conn
