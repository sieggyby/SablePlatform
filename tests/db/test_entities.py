"""Tests for sable_platform.db.entities module."""
from __future__ import annotations

import pytest

from sable_platform.db.entities import (
    add_handle,
    archive_entity,
    create_entity,
    find_entity_by_handle,
    get_entity,
    update_display_name,
)
from sable_platform.errors import ENTITY_ARCHIVED, ENTITY_NOT_FOUND, ORG_NOT_FOUND, SableError


# ---------------------------------------------------------------------------
# create_entity
# ---------------------------------------------------------------------------

class TestCreateEntity:
    def test_create_minimal(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id)
        assert isinstance(eid, str) and len(eid) == 32

    def test_create_with_display_name(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id, display_name="Alice")
        row = get_entity(conn, eid)
        assert row["display_name"] == "Alice"
        assert row["status"] == "candidate"
        assert row["source"] == "auto"

    def test_create_custom_status_and_source(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id, status="confirmed", source="manual")
        row = get_entity(conn, eid)
        assert row["status"] == "confirmed"
        assert row["source"] == "manual"

    def test_create_nonexistent_org_raises(self, in_memory_db):
        with pytest.raises(SableError) as exc_info:
            create_entity(in_memory_db, "ghost_org")
        assert exc_info.value.code == ORG_NOT_FOUND

    def test_create_committed(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id)
        row = conn.execute("SELECT * FROM entities WHERE entity_id=?", (eid,)).fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# get_entity
# ---------------------------------------------------------------------------

class TestGetEntity:
    def test_get_returns_row(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id, display_name="Bob")
        row = get_entity(conn, eid)
        assert row["entity_id"] == eid
        assert row["org_id"] == org_id

    def test_get_nonexistent_raises(self, org_db):
        conn, _ = org_db
        with pytest.raises(SableError) as exc_info:
            get_entity(conn, "nonexistent")
        assert exc_info.value.code == ENTITY_NOT_FOUND


# ---------------------------------------------------------------------------
# find_entity_by_handle
# ---------------------------------------------------------------------------

class TestFindEntityByHandle:
    def test_find_existing(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id, display_name="Alice")
        add_handle(conn, eid, "discord", "alice#1234")
        result = find_entity_by_handle(conn, org_id, "discord", "alice#1234")
        assert result is not None
        assert result["entity_id"] == eid

    def test_find_normalizes_handle(self, org_db):
        """Handles are lowered and @-stripped on both add and find."""
        conn, org_id = org_db
        eid = create_entity(conn, org_id)
        add_handle(conn, eid, "twitter", "@Alice")
        result = find_entity_by_handle(conn, org_id, "twitter", "@ALICE")
        assert result is not None
        assert result["entity_id"] == eid

    def test_find_nonexistent_returns_none(self, org_db):
        conn, org_id = org_db
        assert find_entity_by_handle(conn, org_id, "discord", "ghost") is None

    def test_find_excludes_archived(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id)
        add_handle(conn, eid, "discord", "alice")
        archive_entity(conn, eid)
        assert find_entity_by_handle(conn, org_id, "discord", "alice") is None

    def test_find_scoped_to_org(self, org_db):
        conn, org_id = org_db
        conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('other', 'Other')")
        conn.commit()
        eid = create_entity(conn, org_id)
        add_handle(conn, eid, "discord", "alice")
        assert find_entity_by_handle(conn, "other", "discord", "alice") is None


# ---------------------------------------------------------------------------
# update_display_name
# ---------------------------------------------------------------------------

class TestUpdateDisplayName:
    def test_update_candidate(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id, display_name="Old")
        update_display_name(conn, eid, "New")
        assert get_entity(conn, eid)["display_name"] == "New"

    def test_update_confirmed_auto_source_skipped(self, org_db):
        """Confirmed entity cannot be auto-updated (only manual). updated_at unchanged."""
        conn, org_id = org_db
        eid = create_entity(conn, org_id, display_name="Original", status="confirmed")
        before = get_entity(conn, eid)["updated_at"]
        update_display_name(conn, eid, "Attempted", source="auto")
        row = get_entity(conn, eid)
        assert row["display_name"] == "Original"
        assert row["updated_at"] == before

    def test_update_confirmed_manual_source_allowed(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id, display_name="Original", status="confirmed")
        update_display_name(conn, eid, "Manual Override", source="manual")
        assert get_entity(conn, eid)["display_name"] == "Manual Override"

    def test_update_archived_raises(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id)
        archive_entity(conn, eid)
        with pytest.raises(SableError) as exc_info:
            update_display_name(conn, eid, "Nope")
        assert exc_info.value.code == ENTITY_ARCHIVED

    def test_update_nonexistent_raises(self, org_db):
        conn, _ = org_db
        with pytest.raises(SableError) as exc_info:
            update_display_name(conn, "ghost", "Name")
        assert exc_info.value.code == ENTITY_NOT_FOUND


# ---------------------------------------------------------------------------
# add_handle
# ---------------------------------------------------------------------------

class TestAddHandle:
    def test_add_basic(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id)
        add_handle(conn, eid, "discord", "alice")
        row = conn.execute(
            "SELECT * FROM entity_handles WHERE entity_id=?", (eid,),
        ).fetchone()
        assert row["platform"] == "discord"
        assert row["handle"] == "alice"

    def test_add_normalizes_handle(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id)
        add_handle(conn, eid, "twitter", "@Alice")
        row = conn.execute(
            "SELECT handle FROM entity_handles WHERE entity_id=?", (eid,),
        ).fetchone()
        assert row["handle"] == "alice"

    def test_add_primary(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id)
        add_handle(conn, eid, "discord", "alice", is_primary=True)
        row = conn.execute(
            "SELECT is_primary FROM entity_handles WHERE entity_id=?", (eid,),
        ).fetchone()
        assert row["is_primary"] == 1

    def test_add_duplicate_handle_is_noop(self, org_db):
        """Same platform+handle for same entity silently ignored (IntegrityError caught)."""
        conn, org_id = org_db
        eid = create_entity(conn, org_id)
        add_handle(conn, eid, "discord", "alice")
        add_handle(conn, eid, "discord", "alice")  # no raise
        rows = conn.execute(
            "SELECT * FROM entity_handles WHERE entity_id=?", (eid,),
        ).fetchall()
        assert len(rows) == 1

    def test_add_archived_raises(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id)
        archive_entity(conn, eid)
        with pytest.raises(SableError) as exc_info:
            add_handle(conn, eid, "discord", "alice")
        assert exc_info.value.code == ENTITY_ARCHIVED

    def test_add_shared_handle_creates_merge_candidate(self, org_db):
        """Two entities with same platform+handle should create a merge candidate.

        Note: UNIQUE(platform, handle) is global — the second entity does NOT
        actually get a handle row.  The IntegrityError is caught silently and
        a merge candidate is created instead.  This is by design: the handle
        belongs to one entity until the merge is resolved.
        """
        conn, org_id = org_db
        eid1 = create_entity(conn, org_id, display_name="Alice A")
        add_handle(conn, eid1, "twitter", "alice")
        eid2 = create_entity(conn, org_id, display_name="Alice B")
        add_handle(conn, eid2, "twitter", "alice")
        # Merge candidate exists (IDs are sorted alphabetically by create_merge_candidate)
        row = conn.execute(
            "SELECT * FROM merge_candidates WHERE "
            "(entity_a_id=? AND entity_b_id=?) OR (entity_a_id=? AND entity_b_id=?)",
            (eid1, eid2, eid2, eid1),
        ).fetchone()
        assert row is not None
        assert row["confidence"] == 0.80
        # Second entity does NOT own the handle (global unique constraint)
        handle_row = conn.execute(
            "SELECT entity_id FROM entity_handles WHERE platform='twitter' AND handle='alice'",
        ).fetchone()
        assert handle_row["entity_id"] == eid1  # still belongs to first entity

    def test_add_shared_handle_cross_org_no_merge(self, org_db):
        """Cross-org handle collision: global UNIQUE blocks insert, but no merge candidate."""
        conn, org_id = org_db
        conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('other', 'Other')")
        conn.commit()
        eid1 = create_entity(conn, org_id)
        add_handle(conn, eid1, "discord", "alice")
        eid2 = create_entity(conn, "other")
        add_handle(conn, eid2, "discord", "alice")
        # No merge candidate (cross-org query doesn't find it)
        mc = conn.execute("SELECT * FROM merge_candidates").fetchall()
        assert len(mc) == 0
        # Handle still belongs to first entity
        handle_row = conn.execute(
            "SELECT entity_id FROM entity_handles WHERE platform='discord' AND handle='alice'",
        ).fetchone()
        assert handle_row["entity_id"] == eid1

    def test_add_nonexistent_entity_raises(self, org_db):
        conn, _ = org_db
        with pytest.raises(SableError) as exc_info:
            add_handle(conn, "ghost", "discord", "alice")
        assert exc_info.value.code == ENTITY_NOT_FOUND


# ---------------------------------------------------------------------------
# archive_entity
# ---------------------------------------------------------------------------

class TestArchiveEntity:
    def test_archive(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id)
        archive_entity(conn, eid)
        assert get_entity(conn, eid)["status"] == "archived"

    def test_archive_writes_audit_log(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id)
        archive_entity(conn, eid)
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE action='entity_archive' AND entity_id=?",
            (eid,),
        ).fetchall()
        assert len(rows) == 1

    def test_archive_already_archived_duplicates_audit(self, org_db):
        """Archiving twice succeeds silently and writes a second audit row (current behavior)."""
        conn, org_id = org_db
        eid = create_entity(conn, org_id)
        archive_entity(conn, eid)
        archive_entity(conn, eid)
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE action='entity_archive' AND entity_id=?",
            (eid,),
        ).fetchall()
        # Documents that double-archive writes duplicate audit entries
        assert len(rows) == 2

    def test_archive_nonexistent_raises(self, org_db):
        conn, _ = org_db
        with pytest.raises(SableError) as exc_info:
            archive_entity(conn, "ghost")
        assert exc_info.value.code == ENTITY_NOT_FOUND

    def test_archive_committed(self, org_db):
        conn, org_id = org_db
        eid = create_entity(conn, org_id)
        archive_entity(conn, eid)
        row = conn.execute("SELECT status FROM entities WHERE entity_id=?", (eid,)).fetchone()
        assert row["status"] == "archived"
