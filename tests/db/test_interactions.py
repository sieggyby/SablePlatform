"""Tests for entity interaction edge helpers."""
from __future__ import annotations

import pytest

from sable_platform.db.interactions import (
    sync_interaction_edges,
    list_interactions,
    get_interaction_summary,
)
from sable_platform.errors import SableError, ORG_NOT_FOUND


# --- Migration ---


def test_entity_interactions_table_columns(in_memory_db):
    cols = {row[1] for row in in_memory_db.execute("PRAGMA table_info(entity_interactions)").fetchall()}
    for expected in (
        "id", "org_id", "source_handle", "target_handle",
        "interaction_type", "count", "first_seen", "last_seen", "run_date",
    ):
        assert expected in cols, f"entity_interactions missing column '{expected}'"


# --- sync_interaction_edges ---


def test_sync_inserts_new_edges(org_db):
    conn, org_id = org_db
    edges = [
        {"source_handle": "alice", "target_handle": "bob", "interaction_type": "reply", "count": 3, "first_seen": "2026-03-01", "last_seen": "2026-03-15"},
        {"source_handle": "alice", "target_handle": "carol", "interaction_type": "mention", "count": 1},
    ]
    n = sync_interaction_edges(conn, org_id, edges, "2026-03-15")
    assert n == 2

    rows = conn.execute("SELECT * FROM entity_interactions WHERE org_id=? ORDER BY count DESC", (org_id,)).fetchall()
    assert len(rows) == 2
    assert rows[0]["source_handle"] == "alice"
    assert rows[0]["target_handle"] == "bob"
    assert rows[0]["count"] == 3
    assert rows[0]["run_date"] == "2026-03-15"


def test_sync_upserts_existing_edge(org_db):
    conn, org_id = org_db

    edges1 = [{"source_handle": "alice", "target_handle": "bob", "interaction_type": "reply", "count": 3, "first_seen": "2026-03-01", "last_seen": "2026-03-15"}]
    sync_interaction_edges(conn, org_id, edges1, "2026-03-15")

    edges2 = [{"source_handle": "alice", "target_handle": "bob", "interaction_type": "reply", "count": 2, "first_seen": "2026-03-10", "last_seen": "2026-03-22"}]
    sync_interaction_edges(conn, org_id, edges2, "2026-03-22")

    rows = conn.execute("SELECT * FROM entity_interactions WHERE org_id=?", (org_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["count"] == 5  # 3 + 2
    assert rows[0]["first_seen"] == "2026-03-01"  # earliest preserved
    assert rows[0]["last_seen"] == "2026-03-22"  # latest updated
    assert rows[0]["run_date"] == "2026-03-22"


def test_sync_default_count_is_one(org_db):
    conn, org_id = org_db
    edges = [{"source_handle": "a", "target_handle": "b", "interaction_type": "co_mention"}]
    sync_interaction_edges(conn, org_id, edges, "2026-03-15")

    row = conn.execute("SELECT count FROM entity_interactions WHERE org_id=?", (org_id,)).fetchone()
    assert row["count"] == 1


def test_sync_rejects_unknown_org(in_memory_db):
    edges = [{"source_handle": "a", "target_handle": "b", "interaction_type": "reply"}]
    with pytest.raises(SableError) as exc:
        sync_interaction_edges(in_memory_db, "nonexistent", edges, "2026-03-15")
    assert exc.value.code == ORG_NOT_FOUND


def test_sync_empty_edges_list(org_db):
    conn, org_id = org_db
    n = sync_interaction_edges(conn, org_id, [], "2026-03-15")
    assert n == 0


# --- list_interactions ---


def test_list_interactions_sorted_by_count(org_db):
    conn, org_id = org_db
    edges = [
        {"source_handle": "a", "target_handle": "b", "interaction_type": "reply", "count": 1},
        {"source_handle": "c", "target_handle": "d", "interaction_type": "reply", "count": 10},
        {"source_handle": "e", "target_handle": "f", "interaction_type": "mention", "count": 5},
    ]
    sync_interaction_edges(conn, org_id, edges, "2026-03-15")

    rows = list_interactions(conn, org_id)
    assert len(rows) == 3
    assert rows[0]["count"] == 10
    assert rows[1]["count"] == 5
    assert rows[2]["count"] == 1


def test_list_interactions_filter_by_type(org_db):
    conn, org_id = org_db
    edges = [
        {"source_handle": "a", "target_handle": "b", "interaction_type": "reply", "count": 5},
        {"source_handle": "c", "target_handle": "d", "interaction_type": "mention", "count": 3},
    ]
    sync_interaction_edges(conn, org_id, edges, "2026-03-15")

    rows = list_interactions(conn, org_id, interaction_type="mention")
    assert len(rows) == 1
    assert rows[0]["interaction_type"] == "mention"


def test_list_interactions_min_count_filter(org_db):
    conn, org_id = org_db
    edges = [
        {"source_handle": "a", "target_handle": "b", "interaction_type": "reply", "count": 1},
        {"source_handle": "c", "target_handle": "d", "interaction_type": "reply", "count": 10},
    ]
    sync_interaction_edges(conn, org_id, edges, "2026-03-15")

    rows = list_interactions(conn, org_id, min_count=5)
    assert len(rows) == 1
    assert rows[0]["count"] == 10


# --- get_interaction_summary ---


def test_interaction_summary(org_db):
    conn, org_id = org_db
    edges = [
        {"source_handle": "alice", "target_handle": "bob", "interaction_type": "reply", "count": 3},
        {"source_handle": "alice", "target_handle": "carol", "interaction_type": "mention", "count": 2},
        {"source_handle": "bob", "target_handle": "carol", "interaction_type": "reply", "count": 1},
    ]
    sync_interaction_edges(conn, org_id, edges, "2026-03-15")

    summary = get_interaction_summary(conn, org_id)
    assert summary["edge_count"] == 3
    assert summary["total_interactions"] == 6
    assert summary["unique_sources"] == 2  # alice, bob
    assert summary["unique_targets"] == 2  # bob, carol


def test_interaction_summary_empty_org(org_db):
    conn, org_id = org_db
    summary = get_interaction_summary(conn, org_id)
    assert summary["edge_count"] == 0
    assert summary["total_interactions"] == 0
