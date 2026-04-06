"""Tests for Member Journey Tracking (Feature 3)."""
from __future__ import annotations

import uuid

import pytest

from sable_platform.db.tags import add_tag, get_active_tags
from sable_platform.db.journey import get_entity_journey, entity_funnel, first_seen_list
from sable_platform.db.actions import create_action, complete_action


def _make_entity(conn, org_id, display_name="Test Person"):
    entity_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO entities (entity_id, org_id, display_name, source, status)
        VALUES (?, ?, ?, 'cult_doctor', 'confirmed')
        """,
        (entity_id, org_id, display_name),
    )
    conn.commit()
    return entity_id


# ---------------------------------------------------------------------------
# Tag history writes
# ---------------------------------------------------------------------------

def test_add_new_tag_writes_added_history(org_db):
    conn, org_id = org_db
    entity_id = _make_entity(conn, org_id)
    add_tag(conn, entity_id, "bridge_node", confidence=0.9)

    history = conn.execute(
        "SELECT * FROM entity_tag_history WHERE entity_id=?", (entity_id,)
    ).fetchall()
    assert len(history) == 1
    assert history[0]["change_type"] == "added"
    assert history[0]["tag"] == "bridge_node"
    assert history[0]["confidence"] == pytest.approx(0.9)


def test_replace_tag_writes_replaced_then_added(org_db):
    conn, org_id = org_db
    entity_id = _make_entity(conn, org_id)

    # Add initial tag (top_contributor is in _REPLACE_CURRENT_TAGS)
    add_tag(conn, entity_id, "top_contributor", confidence=0.7)
    # Add again — should replace
    add_tag(conn, entity_id, "top_contributor", confidence=0.95)

    history = conn.execute(
        "SELECT * FROM entity_tag_history WHERE entity_id=? ORDER BY rowid",
        (entity_id,),
    ).fetchall()
    change_types = [h["change_type"] for h in history]
    assert change_types == ["added", "replaced", "added"]
    # The last 'replaced' corresponds to the first tag being deactivated
    assert history[1]["confidence"] == pytest.approx(0.7)
    assert history[2]["confidence"] == pytest.approx(0.95)


def test_non_replace_tag_appends_without_replacing(org_db):
    """Tags NOT in _REPLACE_CURRENT_TAGS accumulate without replacing."""
    conn, org_id = org_db
    entity_id = _make_entity(conn, org_id)

    add_tag(conn, entity_id, "cultist_candidate", confidence=0.8)
    add_tag(conn, entity_id, "cultist_candidate", confidence=0.9)

    history = conn.execute(
        "SELECT change_type FROM entity_tag_history WHERE entity_id=? ORDER BY rowid",
        (entity_id,),
    ).fetchall()
    # Both are 'added' — cultist_candidate is not in REPLACE_CURRENT_TAGS
    change_types = [h["change_type"] for h in history]
    assert change_types == ["added", "added"]


# ---------------------------------------------------------------------------
# Entity journey
# ---------------------------------------------------------------------------

def test_get_entity_journey_chronological(org_db):
    conn, org_id = org_db
    entity_id = _make_entity(conn, org_id, "Alice")

    add_tag(conn, entity_id, "cultist_candidate", confidence=0.8)
    action_id = create_action(conn, org_id, "DM Alice", entity_id=entity_id)
    complete_action(conn, action_id, outcome_notes="responded positively")

    events = get_entity_journey(conn, entity_id)
    event_types = [e["type"] for e in events]

    assert "first_seen" in event_types
    assert "tag_change" in event_types
    assert "action" in event_types

    # All timestamps should be in ascending order
    timestamps = [e.get("timestamp", "") for e in events if e.get("timestamp")]
    assert timestamps == sorted(timestamps)


def test_get_entity_journey_empty_entity(org_db):
    conn, org_id = org_db
    entity_id = _make_entity(conn, org_id)
    events = get_entity_journey(conn, entity_id)
    # At minimum, first_seen event from entity creation
    assert len(events) >= 1
    assert events[0]["type"] == "first_seen"


def test_entity_funnel_counts(org_db):
    conn, org_id = org_db
    e1 = _make_entity(conn, org_id, "Entity 1")
    e2 = _make_entity(conn, org_id, "Entity 2")
    e3 = _make_entity(conn, org_id, "Entity 3")

    add_tag(conn, e1, "cultist_candidate", confidence=0.8)
    add_tag(conn, e2, "cultist_candidate", confidence=0.7)
    add_tag(conn, e2, "top_contributor")

    f = entity_funnel(conn, org_id)
    assert f["total_entities"] == 3
    assert f["cultist_candidate_count"] == 2
    assert f["top_contributor_count"] == 1


def test_first_seen_list_filter_source(org_db):
    conn, org_id = org_db
    # Insert entities with different sources
    conn.execute(
        "INSERT INTO entities (entity_id, org_id, display_name, source, status) VALUES (?, ?, 'A', 'cult_doctor', 'confirmed')",
        (uuid.uuid4().hex, org_id),
    )
    conn.execute(
        "INSERT INTO entities (entity_id, org_id, display_name, source, status) VALUES (?, ?, 'B', 'sable_tracking', 'confirmed')",
        (uuid.uuid4().hex, org_id),
    )
    conn.commit()

    cult_doctor_rows = first_seen_list(conn, org_id, source="cult_doctor")
    tracking_rows = first_seen_list(conn, org_id, source="sable_tracking")

    assert all(r["source"] == "cult_doctor" for r in cult_doctor_rows)
    assert all(r["source"] == "sable_tracking" for r in tracking_rows)


def test_entity_funnel_empty_org(org_db):
    conn, org_id = org_db
    f = entity_funnel(conn, org_id)
    assert f["total_entities"] == 0
    assert f["cultist_candidate_count"] == 0
