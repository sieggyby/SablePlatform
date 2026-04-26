"""Tests for metric_snapshots CRUD."""
from __future__ import annotations

import pytest

from sable_platform.db.connection import get_db
from sable_platform.db.snapshots import (
    get_latest_snapshot,
    get_snapshot,
    list_snapshots,
    upsert_metric_snapshot,
)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "sable.db"
    c = get_db(db_path=str(db_path))
    # Seed an org so FK is satisfiable.
    c.execute(
        "INSERT INTO orgs (org_id, display_name, status) VALUES (?, ?, ?)",
        ("tig", "TIG", "active"),
    )
    c.commit()
    yield c
    c.close()


def test_upsert_creates_new_row(conn):
    rid = upsert_metric_snapshot(
        conn, "tig", "2026-05-01",
        {"followers": 8538, "team_reply_rate": 0.0047},
        source="pipeline",
    )
    assert rid > 0

    got = get_snapshot(conn, "tig", "2026-05-01")
    assert got is not None
    assert got["org_id"] == "tig"
    assert got["snapshot_date"] == "2026-05-01"
    assert got["metrics"] == {"followers": 8538, "team_reply_rate": 0.0047}
    assert got["source"] == "pipeline"


def test_upsert_updates_existing_row(conn):
    rid1 = upsert_metric_snapshot(
        conn, "tig", "2026-05-01", {"followers": 8538}, source="pipeline",
    )
    rid2 = upsert_metric_snapshot(
        conn, "tig", "2026-05-01", {"followers": 8600}, source="manual",
    )
    assert rid1 == rid2  # same row, updated

    got = get_snapshot(conn, "tig", "2026-05-01")
    assert got["metrics"] == {"followers": 8600}
    assert got["source"] == "manual"


def test_get_snapshot_returns_none_for_missing(conn):
    assert get_snapshot(conn, "tig", "2030-01-01") is None


def test_get_latest_snapshot_with_before_date(conn):
    upsert_metric_snapshot(conn, "tig", "2026-05-01", {"v": 1}, source="pipeline")
    upsert_metric_snapshot(conn, "tig", "2026-05-08", {"v": 2}, source="pipeline")
    upsert_metric_snapshot(conn, "tig", "2026-05-15", {"v": 3}, source="pipeline")

    # Latest unconditioned
    latest = get_latest_snapshot(conn, "tig")
    assert latest["snapshot_date"] == "2026-05-15"

    # Strictly before 2026-05-15 — should pick 05-08
    prior = get_latest_snapshot(conn, "tig", before_date="2026-05-15")
    assert prior["snapshot_date"] == "2026-05-08"
    assert prior["metrics"] == {"v": 2}


def test_get_latest_snapshot_returns_none_for_unknown_org(conn):
    assert get_latest_snapshot(conn, "nonexistent") is None


def test_list_snapshots_orders_newest_first(conn):
    upsert_metric_snapshot(conn, "tig", "2026-05-01", {"v": 1}, source="pipeline")
    upsert_metric_snapshot(conn, "tig", "2026-05-08", {"v": 2}, source="pipeline")
    upsert_metric_snapshot(conn, "tig", "2026-05-15", {"v": 3}, source="pipeline")

    rows = list_snapshots(conn, "tig", limit=10)
    assert [r["snapshot_date"] for r in rows] == [
        "2026-05-15", "2026-05-08", "2026-05-01",
    ]


def test_list_snapshots_respects_limit(conn):
    for d in ["2026-05-01", "2026-05-08", "2026-05-15"]:
        upsert_metric_snapshot(conn, "tig", d, {"v": d}, source="pipeline")
    rows = list_snapshots(conn, "tig", limit=2)
    assert len(rows) == 2
    assert rows[0]["snapshot_date"] == "2026-05-15"


def test_metrics_round_trip_preserves_nested_structures(conn):
    metrics = {
        "tier_1": {"fletcher_followers": None, "tig_followers": 8538},
        "tier_2": {"team_reply_rate": 0.0047, "lateral_reply_count": 0},
        "tier_2_subsquads_named": ["turtle_protocol_core"],
    }
    upsert_metric_snapshot(conn, "tig", "2026-05-01", metrics, source="pipeline")
    got = get_snapshot(conn, "tig", "2026-05-01")
    assert got["metrics"] == metrics
