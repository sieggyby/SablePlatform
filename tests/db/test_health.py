"""Tests for SP-4: health check query."""
from __future__ import annotations

import pytest

from tests.conftest import make_test_conn
from sable_platform.db.health import check_db_health


@pytest.fixture
def health_db():
    conn = make_test_conn()
    # Simulate ensure_schema() populating schema_version
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (30,))
    conn.commit()
    yield conn
    conn.close()


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
