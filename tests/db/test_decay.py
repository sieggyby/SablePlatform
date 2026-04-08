"""Tests for entity decay score helpers."""
from __future__ import annotations

import json

import pytest

from tests.conftest import make_test_conn
from sable_platform.db.decay import (
    sync_decay_scores,
    list_decay_scores,
    get_decay_summary,
)
from sable_platform.errors import SableError, ORG_NOT_FOUND


def _make_conn():
    return make_test_conn()


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


# --- Migration ---


def test_entity_decay_scores_table_columns():
    conn = _make_conn()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(entity_decay_scores)").fetchall()}
    for expected in (
        "id", "org_id", "entity_id", "decay_score", "risk_tier",
        "scored_at", "run_date", "factors_json",
    ):
        assert expected in cols, f"entity_decay_scores missing column '{expected}'"


# --- sync_decay_scores ---


def test_sync_inserts_new_scores():
    conn = _make_conn()
    org_id = _insert_org(conn)
    scores = [
        {"handle": "alice", "decay_score": 0.7, "risk_tier": "high"},
        {"handle": "bob", "decay_score": 0.3, "risk_tier": "low"},
    ]
    n = sync_decay_scores(conn, org_id, scores, "2026-04-01")
    assert n == 2

    rows = conn.execute(
        "SELECT * FROM entity_decay_scores WHERE org_id=? ORDER BY decay_score DESC",
        (org_id,),
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["decay_score"] == 0.7
    assert rows[0]["run_date"] == "2026-04-01"


def test_sync_upserts_existing_score():
    conn = _make_conn()
    org_id = _insert_org(conn)

    sync_decay_scores(conn, org_id, [
        {"handle": "alice", "decay_score": 0.5, "risk_tier": "medium"},
    ], "2026-03-15")

    sync_decay_scores(conn, org_id, [
        {"handle": "alice", "decay_score": 0.8, "risk_tier": "critical"},
    ], "2026-04-01")

    rows = conn.execute("SELECT * FROM entity_decay_scores WHERE org_id=?", (org_id,)).fetchall()
    assert len(rows) == 1  # upserted, not duplicated
    assert rows[0]["decay_score"] == 0.8
    assert rows[0]["risk_tier"] == "critical"
    assert rows[0]["run_date"] == "2026-04-01"


def test_sync_resolves_handle_to_entity_id():
    conn = _make_conn()
    org_id = _insert_org(conn)
    _insert_entity_with_handle(conn, org_id, "ent_alice_123", "alice")

    sync_decay_scores(conn, org_id, [
        {"handle": "alice", "decay_score": 0.6, "risk_tier": "high"},
    ], "2026-04-01")

    row = conn.execute("SELECT entity_id FROM entity_decay_scores WHERE org_id=?", (org_id,)).fetchone()
    assert row["entity_id"] == "ent_alice_123"  # resolved, not raw handle


def test_sync_falls_back_to_handle_when_no_entity():
    conn = _make_conn()
    org_id = _insert_org(conn)

    sync_decay_scores(conn, org_id, [
        {"handle": "unknown_user", "decay_score": 0.5, "risk_tier": "medium"},
    ], "2026-04-01")

    row = conn.execute("SELECT entity_id FROM entity_decay_scores WHERE org_id=?", (org_id,)).fetchone()
    assert row["entity_id"] == "unknown_user"  # raw handle stored


def test_sync_rejects_unknown_org():
    conn = _make_conn()
    with pytest.raises(SableError) as exc:
        sync_decay_scores(conn, "nonexistent", [
            {"handle": "x", "decay_score": 0.5, "risk_tier": "medium"},
        ], "2026-04-01")
    assert exc.value.code == ORG_NOT_FOUND


def test_sync_empty_scores_list():
    conn = _make_conn()
    org_id = _insert_org(conn)
    n = sync_decay_scores(conn, org_id, [], "2026-04-01")
    assert n == 0


def test_sync_stores_factors_json():
    conn = _make_conn()
    org_id = _insert_org(conn)
    factors = {"activity_drop": 0.4, "sentiment_drift": 0.2, "graph_thinning": 0.1}
    sync_decay_scores(conn, org_id, [
        {"handle": "carol", "decay_score": 0.7, "risk_tier": "high", "factors": factors},
    ], "2026-04-01")

    row = conn.execute("SELECT factors_json FROM entity_decay_scores WHERE org_id=?", (org_id,)).fetchone()
    assert json.loads(row["factors_json"]) == factors


# --- list_decay_scores ---


def test_list_sorted_by_score():
    conn = _make_conn()
    org_id = _insert_org(conn)
    sync_decay_scores(conn, org_id, [
        {"handle": "a", "decay_score": 0.3, "risk_tier": "low"},
        {"handle": "b", "decay_score": 0.9, "risk_tier": "critical"},
        {"handle": "c", "decay_score": 0.6, "risk_tier": "high"},
    ], "2026-04-01")

    rows = list_decay_scores(conn, org_id)
    assert len(rows) == 3
    assert rows[0]["decay_score"] == 0.9
    assert rows[1]["decay_score"] == 0.6
    assert rows[2]["decay_score"] == 0.3


def test_list_filter_by_tier():
    conn = _make_conn()
    org_id = _insert_org(conn)
    sync_decay_scores(conn, org_id, [
        {"handle": "a", "decay_score": 0.9, "risk_tier": "critical"},
        {"handle": "b", "decay_score": 0.6, "risk_tier": "high"},
    ], "2026-04-01")

    rows = list_decay_scores(conn, org_id, risk_tier="critical")
    assert len(rows) == 1
    assert rows[0]["risk_tier"] == "critical"


def test_list_min_score_filter():
    conn = _make_conn()
    org_id = _insert_org(conn)
    sync_decay_scores(conn, org_id, [
        {"handle": "a", "decay_score": 0.3, "risk_tier": "low"},
        {"handle": "b", "decay_score": 0.8, "risk_tier": "critical"},
    ], "2026-04-01")

    rows = list_decay_scores(conn, org_id, min_score=0.5)
    assert len(rows) == 1
    assert rows[0]["decay_score"] == 0.8


# --- get_decay_summary ---


def test_decay_summary():
    conn = _make_conn()
    org_id = _insert_org(conn)
    sync_decay_scores(conn, org_id, [
        {"handle": "a", "decay_score": 0.9, "risk_tier": "critical"},
        {"handle": "b", "decay_score": 0.7, "risk_tier": "high"},
        {"handle": "c", "decay_score": 0.4, "risk_tier": "medium"},
        {"handle": "d", "decay_score": 0.1, "risk_tier": "low"},
    ], "2026-04-01")

    s = get_decay_summary(conn, org_id)
    assert s["scored_entities"] == 4
    assert s["critical_count"] == 1
    assert s["high_count"] == 1
    assert s["medium_count"] == 1
    assert s["low_count"] == 1


def test_decay_summary_empty():
    conn = _make_conn()
    org_id = _insert_org(conn)
    s = get_decay_summary(conn, org_id)
    assert s["scored_entities"] == 0
    assert s["avg_score"] == 0.0


# --- QA-requested: handle normalization ---


def test_sync_normalizes_fallback_handle_case():
    """Differently-cased handles for the same unresolved user must upsert, not duplicate."""
    conn = _make_conn()
    org_id = _insert_org(conn)

    sync_decay_scores(conn, org_id, [
        {"handle": "Alice", "decay_score": 0.5, "risk_tier": "medium"},
    ], "2026-03-15")

    sync_decay_scores(conn, org_id, [
        {"handle": "alice", "decay_score": 0.7, "risk_tier": "high"},
    ], "2026-04-01")

    rows = conn.execute("SELECT * FROM entity_decay_scores WHERE org_id=?", (org_id,)).fetchall()
    assert len(rows) == 1  # upserted, not duplicated
    assert rows[0]["decay_score"] == 0.7


# --- QA-requested: malformed score rolls back batch ---


def test_sync_malformed_score_raises():
    """A malformed score dict should raise KeyError."""
    conn = _make_conn()
    org_id = _insert_org(conn)

    valid = {"handle": "alice", "decay_score": 0.5, "risk_tier": "medium"}
    bad = {"bad": "dict"}  # missing required keys

    with pytest.raises(KeyError):
        sync_decay_scores(conn, org_id, [valid, bad], "2026-04-01")
