"""Tests for entity note helpers."""
from __future__ import annotations

import pytest

from sable_platform.db.entities import (
    add_entity_note,
    list_entity_notes,
    create_entity,
    archive_entity,
)
from sable_platform.errors import SableError, ENTITY_NOT_FOUND, ENTITY_ARCHIVED


class TestAddEntityNote:
    def test_add_note_returns_note_id(self, org_db):
        conn, org_id = org_db
        entity_id = create_entity(conn, org_id, display_name="Test Member")
        note_id = add_entity_note(conn, entity_id, "First note", source="test")
        assert isinstance(note_id, int)
        assert note_id > 0
        row = conn.execute(
            "SELECT note_id FROM entity_notes WHERE note_id=?", (note_id,)
        ).fetchone()
        assert row["note_id"] == note_id

    def test_note_persists_in_db(self, org_db):
        conn, org_id = org_db
        entity_id = create_entity(conn, org_id, display_name="Test Member")
        add_entity_note(conn, entity_id, "Important context", source="sable_tracking")

        row = conn.execute(
            "SELECT body, source FROM entity_notes WHERE entity_id=?", (entity_id,)
        ).fetchone()
        assert row["body"] == "Important context"
        assert row["source"] == "sable_tracking"

    def test_multiple_notes_per_entity(self, org_db):
        conn, org_id = org_db
        entity_id = create_entity(conn, org_id, display_name="Test Member")
        add_entity_note(conn, entity_id, "Note 1", source="a")
        add_entity_note(conn, entity_id, "Note 2", source="b")
        add_entity_note(conn, entity_id, "Note 3", source="c")

        count = conn.execute(
            "SELECT COUNT(*) FROM entity_notes WHERE entity_id=?", (entity_id,)
        ).fetchone()[0]
        assert count == 3

    def test_add_note_to_nonexistent_entity_raises(self, org_db):
        conn, _ = org_db
        with pytest.raises(SableError) as exc_info:
            add_entity_note(conn, "nonexistent_entity_id", "body")
        assert exc_info.value.code == ENTITY_NOT_FOUND

    def test_add_note_to_archived_entity_raises(self, org_db):
        conn, org_id = org_db
        entity_id = create_entity(conn, org_id, display_name="Archived")
        archive_entity(conn, entity_id)
        with pytest.raises(SableError) as exc_info:
            add_entity_note(conn, entity_id, "Should fail")
        assert exc_info.value.code == ENTITY_ARCHIVED

    def test_default_source_is_manual(self, org_db):
        conn, org_id = org_db
        entity_id = create_entity(conn, org_id, display_name="Test")
        add_entity_note(conn, entity_id, "No source specified")

        row = conn.execute(
            "SELECT source FROM entity_notes WHERE entity_id=?", (entity_id,)
        ).fetchone()
        assert row["source"] == "manual"


class TestListEntityNotes:
    def test_list_returns_newest_first(self, org_db):
        conn, org_id = org_db
        entity_id = create_entity(conn, org_id, display_name="Test")
        add_entity_note(conn, entity_id, "First")
        add_entity_note(conn, entity_id, "Second")
        add_entity_note(conn, entity_id, "Third")

        notes = list_entity_notes(conn, entity_id)
        assert len(notes) == 3
        # ORDER BY created_at DESC, note_id DESC — tiebreaks on auto-increment
        assert notes[0]["body"] == "Third"
        assert notes[2]["body"] == "First"

    def test_list_respects_limit(self, org_db):
        conn, org_id = org_db
        entity_id = create_entity(conn, org_id, display_name="Test")
        for i in range(5):
            add_entity_note(conn, entity_id, f"Note {i}")

        notes = list_entity_notes(conn, entity_id, limit=2)
        assert len(notes) == 2

    def test_list_empty_for_entity_with_no_notes(self, org_db):
        conn, org_id = org_db
        entity_id = create_entity(conn, org_id, display_name="Test")
        notes = list_entity_notes(conn, entity_id)
        assert notes == []

    def test_list_nonexistent_entity_returns_empty(self, org_db):
        """list_entity_notes returns [] for non-existent entity_id (no raise)."""
        conn, _ = org_db
        notes = list_entity_notes(conn, "nonexistent_entity_id")
        assert notes == []
