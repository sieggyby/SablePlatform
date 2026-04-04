"""Tests for sable_platform.db.journey module."""
from __future__ import annotations

from sable_platform.db.journey import entity_funnel, first_seen_list, get_entity_journey
from sable_platform.db.entities import create_entity
from sable_platform.db.tags import add_tag
from sable_platform.db.actions import create_action, claim_action, complete_action


def _insert_entity(conn, org_id, entity_id=None, display_name="Test", source="auto"):
    """Insert entity and return entity_id."""
    eid = entity_id or "ent_test_1"
    conn.execute(
        "INSERT INTO entities (entity_id, org_id, display_name, status, source) VALUES (?, ?, ?, 'confirmed', ?)",
        (eid, org_id, display_name, source),
    )
    conn.commit()
    return eid


# ---------------------------------------------------------------------------
# get_entity_journey
# ---------------------------------------------------------------------------

class TestGetEntityJourney:
    def test_empty_entity(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        events = get_entity_journey(conn, eid)
        assert len(events) == 1
        assert events[0]["type"] == "first_seen"

    def test_nonexistent_entity(self, org_db):
        conn, _ = org_db
        events = get_entity_journey(conn, "ghost")
        assert events == []

    def test_includes_tag_events(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "cultist", source="grader")
        events = get_entity_journey(conn, eid)
        tag_events = [e for e in events if e["type"] == "tag_change"]
        assert len(tag_events) >= 1
        assert tag_events[0]["tag"] == "cultist"

    def test_includes_action_events(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        aid = create_action(conn, org_id, "DM outreach", entity_id=eid)
        claim_action(conn, aid, "sieggy")
        complete_action(conn, aid, outcome_notes="Positive")
        events = get_entity_journey(conn, eid)
        action_events = [e for e in events if e["type"] == "action"]
        claimed_events = [e for e in events if e["type"] == "action_claimed"]
        completed_events = [e for e in events if e["type"] == "action_completed"]
        assert len(action_events) == 1
        assert len(claimed_events) == 1
        assert len(completed_events) == 1
        assert completed_events[0]["notes"] == "Positive"

    def test_includes_outcome_events(self, org_db):
        from sable_platform.db.outcomes import create_outcome
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        create_outcome(conn, org_id, "entity_converted", entity_id=eid, description="Joined")
        events = get_entity_journey(conn, eid)
        outcome_events = [e for e in events if e["type"] == "outcome"]
        assert len(outcome_events) == 1
        assert outcome_events[0]["outcome_type"] == "entity_converted"

    def test_includes_action_skipped_event(self, org_db):
        from sable_platform.db.actions import skip_action
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        aid = create_action(conn, org_id, "Skip me", entity_id=eid)
        skip_action(conn, aid, outcome_notes="Not relevant")
        events = get_entity_journey(conn, eid)
        skipped = [e for e in events if e["type"] == "action_skipped"]
        assert len(skipped) == 1
        assert skipped[0]["title"] == "Skip me"

    def test_sorted_chronologically(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "cultist")
        events = get_entity_journey(conn, eid)
        timestamps = [e["timestamp"] for e in events if e["timestamp"]]
        assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# entity_funnel
# ---------------------------------------------------------------------------

class TestEntityFunnel:
    def test_empty_org(self, org_db):
        conn, org_id = org_db
        funnel = entity_funnel(conn, org_id)
        assert funnel["total_entities"] == 0
        assert funnel["cultist_candidate_count"] == 0
        assert funnel["top_contributor_count"] == 0

    def test_counts(self, org_db):
        conn, org_id = org_db
        eid1 = _insert_entity(conn, org_id, "ent_a")
        eid2 = _insert_entity(conn, org_id, "ent_b")
        add_tag(conn, eid1, "cultist_candidate")
        add_tag(conn, eid2, "top_contributor")

        funnel = entity_funnel(conn, org_id)
        assert funnel["total_entities"] == 2
        assert funnel["cultist_candidate_count"] == 1
        assert funnel["top_contributor_count"] == 1

    def test_excludes_archived(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        conn.execute("UPDATE entities SET status='archived' WHERE entity_id=?", (eid,))
        conn.commit()
        funnel = entity_funnel(conn, org_id)
        assert funnel["total_entities"] == 0

    def test_avg_days_none_when_no_history(self, org_db):
        conn, org_id = org_db
        funnel = entity_funnel(conn, org_id)
        assert funnel["avg_days_to_cultist"] is None
        assert funnel["avg_days_to_top_contributor"] is None


# ---------------------------------------------------------------------------
# first_seen_list
# ---------------------------------------------------------------------------

class TestFirstSeenList:
    def test_empty(self, org_db):
        conn, org_id = org_db
        assert first_seen_list(conn, org_id) == []

    def test_returns_entities(self, org_db):
        conn, org_id = org_db
        _insert_entity(conn, org_id, "ent_a", source="tracking")
        _insert_entity(conn, org_id, "ent_b", source="manual")
        rows = first_seen_list(conn, org_id)
        assert len(rows) == 2

    def test_filter_by_source(self, org_db):
        conn, org_id = org_db
        _insert_entity(conn, org_id, "ent_a", source="tracking")
        _insert_entity(conn, org_id, "ent_b", source="manual")
        rows = first_seen_list(conn, org_id, source="tracking")
        assert len(rows) == 1
        assert rows[0]["source"] == "tracking"

    def test_excludes_archived(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        conn.execute("UPDATE entities SET status='archived' WHERE entity_id=?", (eid,))
        conn.commit()
        assert first_seen_list(conn, org_id) == []

    def test_respects_limit(self, org_db):
        conn, org_id = org_db
        for i in range(5):
            _insert_entity(conn, org_id, f"ent_{i}")
        assert len(first_seen_list(conn, org_id, limit=3)) == 3
