"""Tests for SP-4: health check query."""
from __future__ import annotations

import sqlite3

import pytest

from sable_platform.db.connection import ensure_schema
from sable_platform.db.health import check_db_health


@pytest.fixture
def health_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def test_health_check_fresh_db(health_db):
    """Health check on a fresh DB returns ok with current migration version."""
    result = check_db_health(health_db)
    assert result["ok"] is True
    assert result["migration_version"] == 30
    assert result["org_count"] == 0
    assert result["latest_diagnostic_run"] is None


def test_health_check_with_data(health_db):
    """Health check reflects actual org count."""
    health_db.execute("INSERT INTO orgs (org_id, display_name) VALUES ('o1', 'Org 1')")
    health_db.execute("INSERT INTO orgs (org_id, display_name) VALUES ('o2', 'Org 2')")
    health_db.commit()

    result = check_db_health(health_db)
    assert result["org_count"] == 2
