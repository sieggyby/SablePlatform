"""Tests for LI-3: prospect graduation."""
from __future__ import annotations

import sqlite3

import pytest

from sable_platform.db.connection import ensure_schema
from sable_platform.db.prospects import (
    graduate_prospect,
    list_prospect_scores,
    sync_prospect_scores,
)


@pytest.fixture
def grad_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    # Seed two prospects
    sync_prospect_scores(conn, [
        {"org_id": "alpha", "composite_score": 0.8, "tier": "Tier 1"},
        {"org_id": "beta", "composite_score": 0.6, "tier": "Tier 2"},
    ], "2026-04-01")
    return conn


def test_list_excludes_graduated_by_default(grad_db):
    """Graduated prospects are hidden from default listing."""
    graduate_prospect(grad_db, "alpha")
    rows = list_prospect_scores(grad_db, run_date="2026-04-01")
    ids = [r["org_id"] for r in rows]
    assert "alpha" not in ids
    assert "beta" in ids


def test_list_includes_graduated_when_requested(grad_db):
    """include_graduated=True shows all prospects."""
    graduate_prospect(grad_db, "alpha")
    rows = list_prospect_scores(grad_db, run_date="2026-04-01", include_graduated=True)
    ids = [r["org_id"] for r in rows]
    assert "alpha" in ids
    assert "beta" in ids


def test_graduate_stamps_rows(grad_db):
    """graduate_prospect returns count and stamps graduated_at."""
    count = graduate_prospect(grad_db, "alpha")
    assert count == 1
    row = grad_db.execute(
        "SELECT graduated_at FROM prospect_scores WHERE org_id='alpha'"
    ).fetchone()
    assert row["graduated_at"] is not None


def test_graduate_nonexistent_returns_zero(grad_db):
    """Graduating a non-existent project returns 0."""
    count = graduate_prospect(grad_db, "nonexistent")
    assert count == 0


def test_graduate_idempotent(grad_db):
    """Graduating twice does not re-stamp already-graduated rows."""
    graduate_prospect(grad_db, "alpha")
    count = graduate_prospect(grad_db, "alpha")
    assert count == 0  # already graduated


def test_graduated_at_column_exists(grad_db):
    """Migration 025 adds graduated_at column."""
    row = grad_db.execute(
        "SELECT graduated_at FROM prospect_scores LIMIT 1"
    ).fetchone()
    assert row["graduated_at"] is None  # default is NULL


def test_future_sync_keeps_graduated_hidden(grad_db):
    """A later rescore should inherit graduated_at and stay hidden by default."""
    graduate_prospect(grad_db, "alpha")
    sync_prospect_scores(
        grad_db,
        [{"org_id": "alpha", "composite_score": 0.9, "tier": "Tier 1"}],
        "2026-04-02",
    )

    rows = list_prospect_scores(grad_db, run_date="2026-04-02")
    assert rows == []

    row = grad_db.execute(
        "SELECT graduated_at FROM prospect_scores WHERE org_id='alpha' AND run_date='2026-04-02'"
    ).fetchone()
    assert row["graduated_at"] is not None
