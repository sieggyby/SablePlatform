"""Tests for F-REJECT: prospect rejection."""
from __future__ import annotations

import sqlite3

import pytest

from sable_platform.db.connection import ensure_schema
from sable_platform.db.prospects import (
    reject_prospect,
    list_prospect_scores,
    sync_prospect_scores,
)


@pytest.fixture
def rej_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    sync_prospect_scores(conn, [
        {"org_id": "alpha", "composite_score": 0.8, "tier": "Tier 1"},
        {"org_id": "beta", "composite_score": 0.6, "tier": "Tier 2"},
    ], "2026-04-01")
    return conn


def test_reject_stamps_rejected_at(rej_db):
    """reject_prospect returns count and stamps rejected_at."""
    count = reject_prospect(rej_db, "alpha")
    assert count == 1
    row = rej_db.execute(
        "SELECT rejected_at FROM prospect_scores WHERE org_id='alpha'"
    ).fetchone()
    assert row["rejected_at"] is not None


def test_reject_nonexistent_returns_zero(rej_db):
    """Rejecting a non-existent project returns 0."""
    assert reject_prospect(rej_db, "nonexistent") == 0


def test_reject_idempotent(rej_db):
    """Rejecting twice does not re-stamp already-rejected rows."""
    reject_prospect(rej_db, "alpha")
    count = reject_prospect(rej_db, "alpha")
    assert count == 0


def test_list_excludes_rejected_by_default(rej_db):
    """Rejected prospects are hidden from default listing."""
    reject_prospect(rej_db, "alpha")
    rows = list_prospect_scores(rej_db, run_date="2026-04-01")
    ids = [r["org_id"] for r in rows]
    assert "alpha" not in ids
    assert "beta" in ids


def test_list_includes_rejected_when_requested(rej_db):
    """include_rejected=True shows rejected prospects."""
    reject_prospect(rej_db, "alpha")
    rows = list_prospect_scores(rej_db, run_date="2026-04-01", include_rejected=True)
    ids = [r["org_id"] for r in rows]
    assert "alpha" in ids
    assert "beta" in ids


def test_rejected_at_column_exists(rej_db):
    """Migration 026 adds rejected_at column."""
    row = rej_db.execute(
        "SELECT rejected_at FROM prospect_scores LIMIT 1"
    ).fetchone()
    assert row["rejected_at"] is None


def test_reject_after_graduate_still_works(rej_db):
    """A graduated prospect can still be rejected (dual lifecycle state)."""
    from sable_platform.db.prospects import graduate_prospect
    graduate_prospect(rej_db, "alpha")
    count = reject_prospect(rej_db, "alpha")
    assert count == 1
    row = rej_db.execute(
        "SELECT graduated_at, rejected_at FROM prospect_scores WHERE org_id='alpha'"
    ).fetchone()
    assert row["graduated_at"] is not None
    assert row["rejected_at"] is not None


def test_reject_audit_logged(rej_db):
    """Rejection should be auditable via log_audit (tested at CLI level)."""
    from sable_platform.db.audit import log_audit, list_audit_log
    # Seed an org for the audit log FK
    rej_db.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('alpha', 'Alpha', 'active')")
    rej_db.commit()

    reject_prospect(rej_db, "alpha")
    log_audit(rej_db, "operator_x", "prospect_rejected",
              org_id="alpha", detail={"project_id": "alpha", "reason": "bad fit"})

    logs = list_audit_log(rej_db, limit=10)
    assert any(l["action"] == "prospect_rejected" for l in logs)


def test_future_sync_keeps_rejected_hidden(rej_db):
    """A later rescore should inherit rejected_at and stay hidden by default."""
    reject_prospect(rej_db, "alpha")
    sync_prospect_scores(
        rej_db,
        [{"org_id": "alpha", "composite_score": 0.9, "tier": "Tier 1"}],
        "2026-04-02",
    )

    rows = list_prospect_scores(rej_db, run_date="2026-04-02")
    assert rows == []

    row = rej_db.execute(
        "SELECT rejected_at FROM prospect_scores WHERE org_id='alpha' AND run_date='2026-04-02'"
    ).fetchone()
    assert row["rejected_at"] is not None
