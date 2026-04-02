"""Tests for deactivate_tag() in tags.py."""
from __future__ import annotations

import sqlite3

from sable_platform.db.connection import ensure_schema
from sable_platform.db.tags import add_tag, deactivate_tag, get_active_tags


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def _insert_entity(conn, org_id="test_org", entity_id="ent_1") -> str:
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


def test_deactivate_active_tag():
    conn = _make_conn()
    eid = _insert_entity(conn)
    add_tag(conn, eid, "top_contributor", source="tracking")

    assert len(get_active_tags(conn, eid)) == 1
    result = deactivate_tag(conn, eid, "top_contributor")
    assert result is True
    assert len(get_active_tags(conn, eid)) == 0


def test_deactivate_nonexistent_tag_returns_false():
    conn = _make_conn()
    eid = _insert_entity(conn)

    result = deactivate_tag(conn, eid, "top_contributor")
    assert result is False


def test_deactivate_already_deactivated_tag_returns_false():
    conn = _make_conn()
    eid = _insert_entity(conn)
    add_tag(conn, eid, "top_contributor", source="tracking")

    deactivate_tag(conn, eid, "top_contributor")
    result = deactivate_tag(conn, eid, "top_contributor")
    assert result is False


def test_deactivate_records_history():
    conn = _make_conn()
    eid = _insert_entity(conn)
    add_tag(conn, eid, "top_contributor", source="tracking", confidence=0.9)

    deactivate_tag(conn, eid, "top_contributor", reason="expired", source="sync_step_10b")

    rows = conn.execute(
        "SELECT * FROM entity_tag_history WHERE entity_id=? AND change_type='expired'",
        (eid,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["tag"] == "top_contributor"
    assert rows[0]["confidence"] == 0.9
    assert rows[0]["source_ref"] == "sync_step_10b"


def test_deactivate_sets_deactivated_at():
    conn = _make_conn()
    eid = _insert_entity(conn)
    add_tag(conn, eid, "top_contributor")

    deactivate_tag(conn, eid, "top_contributor")

    row = conn.execute(
        "SELECT deactivated_at, is_current FROM entity_tags WHERE entity_id=? AND tag='top_contributor'",
        (eid,),
    ).fetchone()
    assert row["is_current"] == 0
    assert row["deactivated_at"] is not None


def test_deactivate_updates_entity_timestamp():
    conn = _make_conn()
    eid = _insert_entity(conn)
    add_tag(conn, eid, "top_contributor")

    before = conn.execute("SELECT updated_at FROM entities WHERE entity_id=?", (eid,)).fetchone()["updated_at"]
    deactivate_tag(conn, eid, "top_contributor")
    after = conn.execute("SELECT updated_at FROM entities WHERE entity_id=?", (eid,)).fetchone()["updated_at"]

    assert after >= before


def test_deactivate_custom_reason():
    conn = _make_conn()
    eid = _insert_entity(conn)
    add_tag(conn, eid, "watchlist_account", source="ops")

    deactivate_tag(conn, eid, "watchlist_account", reason="manual_removal")

    row = conn.execute(
        "SELECT change_type FROM entity_tag_history WHERE entity_id=? AND tag='watchlist_account' AND change_type='manual_removal'",
        (eid,),
    ).fetchone()
    assert row is not None
