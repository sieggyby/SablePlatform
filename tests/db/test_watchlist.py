"""Tests for entity watchlist helpers."""
from __future__ import annotations

import json

import pytest

from tests.conftest import make_test_conn
from sable_platform.db.watchlist import (
    add_to_watchlist,
    get_watchlist_changes,
    list_watchlist,
    remove_from_watchlist,
    take_all_snapshots,
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


def test_watchlist_table_columns():
    conn = _make_conn()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(entity_watchlist)").fetchall()}
    for expected in ("id", "org_id", "entity_id", "added_by", "note", "created_at"):
        assert expected in cols

    cols = {row[1] for row in conn.execute("PRAGMA table_info(watchlist_snapshots)").fetchall()}
    for expected in ("id", "org_id", "entity_id", "decay_score", "tags_json", "interaction_count", "snapshot_at"):
        assert expected in cols


def test_add_to_watchlist():
    conn = _make_conn()
    org_id = _insert_org(conn)
    result = add_to_watchlist(conn, org_id, "alice", "operator1", "test note")
    assert result is True

    row = conn.execute("SELECT * FROM entity_watchlist WHERE org_id=?", (org_id,)).fetchone()
    assert row["entity_id"] == "alice"
    assert row["added_by"] == "operator1"
    assert row["note"] == "test note"


def test_add_duplicate_returns_false():
    conn = _make_conn()
    org_id = _insert_org(conn)
    assert add_to_watchlist(conn, org_id, "alice", "op") is True
    assert add_to_watchlist(conn, org_id, "alice", "op") is False


def test_add_rejects_unknown_org():
    conn = _make_conn()
    with pytest.raises(SableError) as exc:
        add_to_watchlist(conn, "nonexistent", "alice", "op")
    assert exc.value.code == ORG_NOT_FOUND


def test_remove_from_watchlist():
    conn = _make_conn()
    org_id = _insert_org(conn)
    add_to_watchlist(conn, org_id, "alice", "op")
    assert remove_from_watchlist(conn, org_id, "alice") is True

    row = conn.execute("SELECT * FROM entity_watchlist WHERE org_id=?", (org_id,)).fetchone()
    assert row is None


def test_remove_nonexistent_returns_false():
    conn = _make_conn()
    org_id = _insert_org(conn)
    assert remove_from_watchlist(conn, org_id, "ghost") is False


def test_list_watchlist_ordering():
    conn = _make_conn()
    org_id = _insert_org(conn)
    add_to_watchlist(conn, org_id, "a", "op")
    add_to_watchlist(conn, org_id, "b", "op")
    add_to_watchlist(conn, org_id, "c", "op")

    rows = list_watchlist(conn, org_id)
    assert len(rows) == 3
    # All three present (ordering within same second may vary)
    entities = {r["entity_id"] for r in rows}
    assert entities == {"a", "b", "c"}


def test_initial_snapshot_taken_on_add():
    conn = _make_conn()
    org_id = _insert_org(conn)
    add_to_watchlist(conn, org_id, "alice", "op")

    snaps = conn.execute(
        "SELECT * FROM watchlist_snapshots WHERE org_id=? AND entity_id='alice'",
        (org_id,),
    ).fetchall()
    assert len(snaps) == 1


def test_take_all_snapshots():
    conn = _make_conn()
    org_id = _insert_org(conn)
    add_to_watchlist(conn, org_id, "a", "op")
    add_to_watchlist(conn, org_id, "b", "op")

    # Each add takes an initial snapshot, so we have 2.
    # take_all_snapshots adds 2 more.
    count = take_all_snapshots(conn, org_id)
    assert count == 2

    snaps = conn.execute("SELECT * FROM watchlist_snapshots WHERE org_id=?", (org_id,)).fetchall()
    assert len(snaps) == 4  # 2 initial + 2 from take_all


def test_get_changes_decay_shift():
    conn = _make_conn()
    org_id = _insert_org(conn)
    add_to_watchlist(conn, org_id, "alice", "op")

    # Simulate a decay score change
    conn.execute(
        "INSERT INTO entity_decay_scores (org_id, entity_id, decay_score, risk_tier, run_date) VALUES (?, ?, 0.7, 'high', '2026-04-01')",
        (org_id, "alice"),
    )
    conn.commit()

    # Take a new snapshot (decay is now 0.7, initial was None)
    take_all_snapshots(conn, org_id)
    changes = get_watchlist_changes(conn, org_id)

    assert len(changes) == 1
    assert any("decay_score" in c for c in changes[0]["changes"])


def test_get_changes_tag_added():
    conn = _make_conn()
    org_id = _insert_org(conn)

    # Create a real entity for tag operations
    conn.execute(
        "INSERT INTO entities (entity_id, org_id, display_name, status) VALUES (?, ?, ?, ?)",
        ("ent_alice", org_id, "Alice", "confirmed"),
    )
    conn.commit()

    add_to_watchlist(conn, org_id, "ent_alice", "op")

    # Add a tag
    conn.execute(
        "INSERT INTO entity_tags (entity_id, tag, source, confidence, is_current) VALUES (?, ?, ?, ?, 1)",
        ("ent_alice", "cultist_candidate", "test", 1.0),
    )
    conn.commit()

    take_all_snapshots(conn, org_id)
    changes = get_watchlist_changes(conn, org_id)

    assert len(changes) == 1
    assert any("tag added: cultist_candidate" in c for c in changes[0]["changes"])


def test_get_changes_no_change():
    conn = _make_conn()
    org_id = _insert_org(conn)
    add_to_watchlist(conn, org_id, "alice", "op")

    # Take another snapshot — nothing changed
    take_all_snapshots(conn, org_id)
    changes = get_watchlist_changes(conn, org_id)

    # No changes (both snapshots identical)
    assert len(changes) == 0


def test_get_changes_newly_watched():
    conn = _make_conn()
    org_id = _insert_org(conn)
    add_to_watchlist(conn, org_id, "alice", "op")

    # Only 1 snapshot (the initial one)
    changes = get_watchlist_changes(conn, org_id)
    assert len(changes) == 1
    assert changes[0]["changes"] == ["newly watched"]
