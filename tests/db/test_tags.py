"""Tests for sable_platform.db.tags module.

Complements test_deactivate_tag.py with broader coverage of add_tag,
get_active_tags, get_entities_by_tag, replace-on-add behavior, and history.
"""
from __future__ import annotations

import pytest

from sable_platform.db.tags import (
    add_tag,
    deactivate_tag,
    get_active_tags,
    get_entities_by_tag,
)


def _insert_entity(conn, org_id, entity_id="ent_1"):
    conn.execute(
        "INSERT OR IGNORE INTO orgs (org_id, display_name, status) VALUES (?, ?, ?)",
        (org_id, "Test Org", "active"),
    )
    conn.execute(
        "INSERT INTO entities (entity_id, org_id, display_name, status) VALUES (?, ?, ?, ?)",
        (entity_id, org_id, "Test Entity", "confirmed"),
    )
    conn.commit()
    return entity_id


# ---------------------------------------------------------------------------
# add_tag
# ---------------------------------------------------------------------------

class TestAddTag:
    def test_add_basic(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "cultist", source="cult_doctor", confidence=0.95)

        tags = get_active_tags(conn, eid)
        assert len(tags) == 1
        assert tags[0]["tag"] == "cultist"
        assert tags[0]["confidence"] == 0.95
        assert tags[0]["source"] == "cult_doctor"

    def test_add_multiple_different_tags(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "cultist")
        add_tag(conn, eid, "voice")
        assert len(get_active_tags(conn, eid)) == 2

    def test_add_with_expiry(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "cultist", expires_at="2099-12-31T00:00:00")
        tags = get_active_tags(conn, eid)
        assert tags[0]["expires_at"] == "2099-12-31T00:00:00"

    def test_add_replace_tag_deactivates_old(self, org_db):
        """Tags in _REPLACE_CURRENT_TAGS should deactivate previous active instance."""
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "top_contributor", source="v1", confidence=0.8)
        add_tag(conn, eid, "top_contributor", source="v2", confidence=0.95)

        active = get_active_tags(conn, eid)
        assert len(active) == 1
        assert active[0]["source"] == "v2"
        assert active[0]["confidence"] == 0.95

    def test_add_replace_records_history(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "top_contributor", source="v1", confidence=0.8)
        add_tag(conn, eid, "top_contributor", source="v2", confidence=0.95)

        history = conn.execute(
            "SELECT change_type FROM entity_tag_history WHERE entity_id=? ORDER BY rowid",
            (eid,),
        ).fetchall()
        types = [r["change_type"] for r in history]
        # "added" for v1, "replaced" for v1 being replaced, "added" for v2
        assert "replaced" in types
        assert types.count("added") == 2

    def test_add_non_replace_tag_allows_duplicate(self, org_db):
        """Tags NOT in _REPLACE_CURRENT_TAGS can accumulate multiple active rows."""
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "cultist", source="v1")
        add_tag(conn, eid, "cultist", source="v2")
        assert len(get_active_tags(conn, eid)) == 2

    def test_add_updates_entity_timestamp(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        before = conn.execute("SELECT updated_at FROM entities WHERE entity_id=?", (eid,)).fetchone()["updated_at"]
        add_tag(conn, eid, "voice")
        after = conn.execute("SELECT updated_at FROM entities WHERE entity_id=?", (eid,)).fetchone()["updated_at"]
        assert after >= before

    def test_add_committed(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "mvl")
        row = conn.execute(
            "SELECT * FROM entity_tags WHERE entity_id=? AND tag='mvl'", (eid,)
        ).fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# get_active_tags
# ---------------------------------------------------------------------------

class TestGetActiveTags:
    def test_empty(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        assert get_active_tags(conn, eid) == []

    def test_excludes_deactivated(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "cultist")
        add_tag(conn, eid, "voice")
        deactivate_tag(conn, eid, "cultist")
        active = get_active_tags(conn, eid)
        assert len(active) == 1
        assert active[0]["tag"] == "voice"

    def test_excludes_expired(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "cultist", expires_at="2000-01-01T00:00:00")
        assert get_active_tags(conn, eid) == []

    def test_ordered_by_added_at(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "cultist")
        add_tag(conn, eid, "voice")
        tags = get_active_tags(conn, eid)
        assert tags[0]["tag"] == "cultist"
        assert tags[1]["tag"] == "voice"


# ---------------------------------------------------------------------------
# get_entities_by_tag
# ---------------------------------------------------------------------------

class TestGetEntitiesByTag:
    def test_returns_matching(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id, "ent_a")
        add_tag(conn, eid, "cultist")
        results = get_entities_by_tag(conn, org_id, "cultist")
        assert len(results) == 1
        assert results[0]["entity_id"] == "ent_a"

    def test_excludes_deactivated_tags(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "cultist")
        deactivate_tag(conn, eid, "cultist")
        assert get_entities_by_tag(conn, org_id, "cultist") == []

    def test_excludes_archived_entities(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "cultist")
        conn.execute("UPDATE entities SET status='archived' WHERE entity_id=?", (eid,))
        conn.commit()
        assert get_entities_by_tag(conn, org_id, "cultist") == []

    def test_excludes_expired_tags(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "cultist", expires_at="2000-01-01T00:00:00")
        assert get_entities_by_tag(conn, org_id, "cultist") == []

    def test_scoped_to_org(self, org_db):
        conn, org_id = org_db
        eid1 = _insert_entity(conn, org_id, "ent_a")
        add_tag(conn, eid1, "cultist")
        conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('other', 'Other')")
        conn.commit()
        eid2 = _insert_entity(conn, "other", "ent_b")
        add_tag(conn, eid2, "cultist")

        results = get_entities_by_tag(conn, org_id, "cultist")
        assert len(results) == 1
        assert results[0]["entity_id"] == "ent_a"

    def test_deduplicates_multiple_active_rows(self, org_db):
        """If an entity has multiple active rows for same tag, return entity once."""
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        # cultist is NOT in _REPLACE_CURRENT_TAGS, so two active rows
        add_tag(conn, eid, "cultist", source="v1")
        add_tag(conn, eid, "cultist", source="v2")
        results = get_entities_by_tag(conn, org_id, "cultist")
        assert len(results) == 1

    def test_excludes_deactivated_unexpired(self, org_db):
        """Manually deactivated tag with future expiry should not match."""
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "cultist", expires_at="2099-12-31T00:00:00")
        deactivate_tag(conn, eid, "cultist")
        assert get_entities_by_tag(conn, org_id, "cultist") == []


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

class TestTagAuditTrail:
    def test_deactivate_tag_writes_audit_log(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "top_contributor", source="tracking")
        deactivate_tag(conn, eid, "top_contributor", reason="expired", source="sync")

        rows = conn.execute(
            "SELECT * FROM audit_log WHERE action='tag_deactivate' AND entity_id=?",
            (eid,),
        ).fetchall()
        assert len(rows) == 1
        assert "top_contributor" in rows[0]["detail_json"]
        assert "expired" in rows[0]["detail_json"]


# ---------------------------------------------------------------------------
# Replace-tag first-add history
# ---------------------------------------------------------------------------

class TestReplaceTagHistory:
    def test_first_add_no_replaced_history(self, org_db):
        """First add of a _REPLACE_CURRENT_TAGS tag: only 'added', no 'replaced'."""
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "top_contributor", source="v1")

        history = conn.execute(
            "SELECT change_type FROM entity_tag_history WHERE entity_id=?",
            (eid,),
        ).fetchall()
        types = [r["change_type"] for r in history]
        assert types == ["added"]


# ---------------------------------------------------------------------------
# Edge case: add_tag on nonexistent entity
# ---------------------------------------------------------------------------

class TestAddTagEdgeCases:
    def test_add_tag_nonexistent_entity_fk_violation(self, org_db):
        """FK constraint prevents orphan tag row for nonexistent entity."""
        import sqlite3 as _sqlite3
        conn, _ = org_db
        with pytest.raises(_sqlite3.IntegrityError):
            add_tag(conn, "ghost_entity", "cultist")
