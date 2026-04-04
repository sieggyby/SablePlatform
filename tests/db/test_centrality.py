"""Tests for entity centrality score helpers."""
from __future__ import annotations

import sqlite3

import pytest

from sable_platform.db.connection import ensure_schema
from sable_platform.db.centrality import (
    sync_centrality_scores,
    list_centrality_scores,
    get_centrality_summary,
)
from sable_platform.errors import SableError, ORG_NOT_FOUND


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def _insert_org(conn, org_id="test_org") -> str:
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, status) VALUES (?, ?, ?)",
        (org_id, "Test Org", "active"),
    )
    conn.commit()
    return org_id


def _insert_entity_with_handle(conn, org_id, entity_id, handle):
    conn.execute(
        "INSERT INTO entities (entity_id, org_id, display_name, status) VALUES (?, ?, ?, ?)",
        (entity_id, org_id, handle, "confirmed"),
    )
    conn.execute(
        "INSERT INTO entity_handles (entity_id, platform, handle, is_primary) VALUES (?, ?, ?, 1)",
        (entity_id, "discord", handle.lower()),
    )
    conn.commit()


def test_entity_centrality_scores_table_columns():
    conn = _make_conn()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(entity_centrality_scores)").fetchall()}
    for expected in (
        "id", "org_id", "entity_id", "degree_centrality",
        "betweenness_centrality", "eigenvector_centrality", "scored_at", "run_date",
    ):
        assert expected in cols, f"entity_centrality_scores missing column '{expected}'"


def test_sync_inserts_new_scores():
    conn = _make_conn()
    org_id = _insert_org(conn)
    scores = [
        {"handle": "alice", "degree": 0.8, "betweenness": 0.5, "eigenvector": 0.3},
        {"handle": "bob", "degree": 0.4, "betweenness": 0.1, "eigenvector": 0.2},
    ]
    n = sync_centrality_scores(conn, org_id, scores, "2026-04-01")
    assert n == 2

    rows = conn.execute(
        "SELECT * FROM entity_centrality_scores WHERE org_id=? ORDER BY betweenness_centrality DESC",
        (org_id,),
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["betweenness_centrality"] == 0.5


def test_sync_upserts_existing():
    conn = _make_conn()
    org_id = _insert_org(conn)

    sync_centrality_scores(conn, org_id, [
        {"handle": "alice", "degree": 0.5, "betweenness": 0.3, "eigenvector": 0.1},
    ], "2026-03-15")

    sync_centrality_scores(conn, org_id, [
        {"handle": "alice", "degree": 0.8, "betweenness": 0.6, "eigenvector": 0.4},
    ], "2026-04-01")

    rows = conn.execute("SELECT * FROM entity_centrality_scores WHERE org_id=?", (org_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["betweenness_centrality"] == 0.6
    assert rows[0]["run_date"] == "2026-04-01"


def test_sync_resolves_handle_to_entity_id():
    conn = _make_conn()
    org_id = _insert_org(conn)
    _insert_entity_with_handle(conn, org_id, "ent_alice_123", "alice")

    sync_centrality_scores(conn, org_id, [
        {"handle": "alice", "degree": 0.5, "betweenness": 0.3, "eigenvector": 0.1},
    ], "2026-04-01")

    row = conn.execute("SELECT entity_id FROM entity_centrality_scores WHERE org_id=?", (org_id,)).fetchone()
    assert row["entity_id"] == "ent_alice_123"


def test_sync_falls_back_to_handle():
    conn = _make_conn()
    org_id = _insert_org(conn)

    sync_centrality_scores(conn, org_id, [
        {"handle": "Unknown_User", "degree": 0.5, "betweenness": 0.3, "eigenvector": 0.1},
    ], "2026-04-01")

    row = conn.execute("SELECT entity_id FROM entity_centrality_scores WHERE org_id=?", (org_id,)).fetchone()
    assert row["entity_id"] == "unknown_user"


def test_sync_rejects_unknown_org():
    conn = _make_conn()
    with pytest.raises(SableError) as exc:
        sync_centrality_scores(conn, "nonexistent", [
            {"handle": "x", "degree": 0.5, "betweenness": 0.3, "eigenvector": 0.1},
        ], "2026-04-01")
    assert exc.value.code == ORG_NOT_FOUND


def test_sync_empty_list():
    conn = _make_conn()
    org_id = _insert_org(conn)
    n = sync_centrality_scores(conn, org_id, [], "2026-04-01")
    assert n == 0


def test_list_sorted_by_betweenness():
    conn = _make_conn()
    org_id = _insert_org(conn)
    sync_centrality_scores(conn, org_id, [
        {"handle": "a", "degree": 0.3, "betweenness": 0.1, "eigenvector": 0.1},
        {"handle": "b", "degree": 0.5, "betweenness": 0.9, "eigenvector": 0.5},
        {"handle": "c", "degree": 0.7, "betweenness": 0.5, "eigenvector": 0.3},
    ], "2026-04-01")

    rows = list_centrality_scores(conn, org_id)
    assert rows[0]["betweenness_centrality"] == 0.9
    assert rows[1]["betweenness_centrality"] == 0.5
    assert rows[2]["betweenness_centrality"] == 0.1


def test_list_min_degree_filter():
    conn = _make_conn()
    org_id = _insert_org(conn)
    sync_centrality_scores(conn, org_id, [
        {"handle": "a", "degree": 0.2, "betweenness": 0.5, "eigenvector": 0.1},
        {"handle": "b", "degree": 0.8, "betweenness": 0.3, "eigenvector": 0.5},
    ], "2026-04-01")

    rows = list_centrality_scores(conn, org_id, min_degree=0.5)
    assert len(rows) == 1
    assert rows[0]["entity_id"] == "b"


def test_centrality_summary():
    conn = _make_conn()
    org_id = _insert_org(conn)
    sync_centrality_scores(conn, org_id, [
        {"handle": "a", "degree": 0.4, "betweenness": 0.2, "eigenvector": 0.1},
        {"handle": "b", "degree": 0.8, "betweenness": 0.6, "eigenvector": 0.5},
    ], "2026-04-01")

    s = get_centrality_summary(conn, org_id)
    assert s["scored_entities"] == 2
    assert s["avg_degree"] == 0.6
    assert s["max_betweenness_entity"] == "b"


def test_centrality_summary_empty():
    conn = _make_conn()
    org_id = _insert_org(conn)
    s = get_centrality_summary(conn, org_id)
    assert s["scored_entities"] == 0
    assert s["avg_degree"] == 0.0
    assert s["max_betweenness_entity"] is None
