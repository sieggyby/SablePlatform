"""Tests for db/merge.py — entity merge candidates and execution."""
from __future__ import annotations

import pytest

from sable_platform.db.merge import (
    MERGE_CONFIDENCE_THRESHOLD,
    create_merge_candidate,
    get_pending_merges,
    execute_merge,
    reconsider_expired_merges,
)
from sable_platform.db.entities import create_entity
from sable_platform.db.tags import _REPLACE_CURRENT_TAGS
from sable_platform.errors import SableError, CROSS_ORG_MERGE_BLOCKED, ENTITY_NOT_FOUND


def _make_org(conn, org_id: str = "merge_org") -> str:
    conn.execute(
        "INSERT OR IGNORE INTO orgs (org_id, display_name) VALUES (?, ?)",
        (org_id, org_id),
    )
    conn.commit()
    return org_id


def _add_handle(conn, entity_id: str, platform: str, handle: str) -> None:
    conn.execute(
        "INSERT INTO entity_handles (entity_id, platform, handle) VALUES (?, ?, ?)",
        (entity_id, platform, handle),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# create_merge_candidate
# ---------------------------------------------------------------------------

def test_create_merge_candidate_pending_above_threshold(in_memory_db):
    conn = in_memory_db
    org_id = _make_org(conn)
    a = create_entity(conn, org_id, "Alice")
    b = create_entity(conn, org_id, "Bob")

    create_merge_candidate(conn, a, b, confidence=0.80, reason="same handle")

    row = conn.execute("SELECT status, confidence FROM merge_candidates").fetchone()
    assert row["status"] == "pending"
    assert row["confidence"] == pytest.approx(0.80)


def test_create_merge_candidate_expired_below_threshold(in_memory_db):
    conn = in_memory_db
    org_id = _make_org(conn)
    a = create_entity(conn, org_id, "Alice2")
    b = create_entity(conn, org_id, "Bob2")

    create_merge_candidate(conn, a, b, confidence=0.50)

    row = conn.execute("SELECT status FROM merge_candidates").fetchone()
    assert row["status"] == "expired"


def test_create_merge_candidate_duplicate_ignored(in_memory_db):
    conn = in_memory_db
    org_id = _make_org(conn)
    a = create_entity(conn, org_id, "Alice3")
    b = create_entity(conn, org_id, "Bob3")

    create_merge_candidate(conn, a, b, confidence=0.75)
    create_merge_candidate(conn, a, b, confidence=0.75)  # should be ignored

    count = conn.execute("SELECT COUNT(*) FROM merge_candidates").fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# get_pending_merges
# ---------------------------------------------------------------------------

def test_get_pending_merges_returns_pending_only(in_memory_db):
    conn = in_memory_db
    org_id = _make_org(conn)
    a = create_entity(conn, org_id, "Alice4")
    b = create_entity(conn, org_id, "Bob4")
    c = create_entity(conn, org_id, "Carol")

    create_merge_candidate(conn, a, b, confidence=0.80)  # pending
    create_merge_candidate(conn, a, c, confidence=0.40)  # expired

    rows = get_pending_merges(conn, org_id)
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"


# ---------------------------------------------------------------------------
# execute_merge
# ---------------------------------------------------------------------------

def test_execute_merge_happy_path(in_memory_db):
    conn = in_memory_db
    org_id = _make_org(conn)
    source_id = create_entity(conn, org_id, "Source")
    target_id = create_entity(conn, org_id, "Target")
    _add_handle(conn, source_id, "twitter", "source_handle")

    execute_merge(conn, source_id, target_id, merged_by="tester")

    source_row = conn.execute("SELECT status FROM entities WHERE entity_id=?", (source_id,)).fetchone()
    assert source_row["status"] == "archived"

    handle_row = conn.execute(
        "SELECT entity_id FROM entity_handles WHERE handle='source_handle'",
    ).fetchone()
    assert handle_row["entity_id"] == target_id

    merge_event = conn.execute(
        "SELECT * FROM merge_events WHERE source_entity_id=?", (source_id,)
    ).fetchone()
    assert merge_event is not None
    assert merge_event["target_entity_id"] == target_id
    assert merge_event["merged_by"] == "tester"


def test_execute_merge_cross_org_blocked(in_memory_db):
    conn = in_memory_db
    _make_org(conn, "org_a")
    _make_org(conn, "org_b")
    source_id = create_entity(conn, "org_a", "Source")
    target_id = create_entity(conn, "org_b", "Target")

    with pytest.raises(SableError) as exc_info:
        execute_merge(conn, source_id, target_id)

    assert exc_info.value.code == CROSS_ORG_MERGE_BLOCKED


def test_execute_merge_source_not_found(in_memory_db):
    conn = in_memory_db
    org_id = _make_org(conn)
    target_id = create_entity(conn, org_id, "Target")

    with pytest.raises(SableError) as exc_info:
        execute_merge(conn, "nonexistent_entity_id", target_id)

    assert exc_info.value.code == ENTITY_NOT_FOUND


# ---------------------------------------------------------------------------
# reconsider_expired_merges
# ---------------------------------------------------------------------------

def test_reconsider_expired_merges(in_memory_db):
    conn = in_memory_db
    org_id = _make_org(conn)
    a = create_entity(conn, org_id, "Alice5")
    b = create_entity(conn, org_id, "Bob5")

    create_merge_candidate(conn, a, b, confidence=0.75)  # above threshold → pending

    # Force to expired
    conn.execute("UPDATE merge_candidates SET status='expired'")
    conn.commit()

    count = reconsider_expired_merges(org_id, conn, threshold=MERGE_CONFIDENCE_THRESHOLD)
    assert count == 1

    row = conn.execute("SELECT status FROM merge_candidates").fetchone()
    assert row["status"] == "pending"
