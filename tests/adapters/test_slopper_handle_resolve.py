"""Tests for SlopperAdvisoryAdapter handle resolution."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from sable_platform.adapters.slopper import SlopperAdvisoryAdapter
from sable_platform.db.connection import ensure_schema
from sable_platform.db.entities import create_entity, add_handle
from sable_platform.errors import SableError, INVALID_CONFIG


@pytest.fixture
def adapter_db(in_memory_db):
    """DB with org + entity + twitter handle."""
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('solstitch', 'SolStitch')")
    conn.commit()
    entity_id = create_entity(conn, "solstitch", display_name="SolStitch Account")
    add_handle(conn, entity_id, "twitter", "solstitchxyz", is_primary=True)
    return conn


def test_resolve_primary_handle(adapter_db):
    adapter = SlopperAdvisoryAdapter()
    with patch("sable_platform.adapters.slopper.get_db", return_value=adapter_db):
        handle = adapter._resolve_primary_handle("solstitch")
    assert handle == "@solstitchxyz"


def test_resolve_falls_back_to_non_primary(in_memory_db):
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('test_org', 'Test')")
    conn.commit()
    entity_id = create_entity(conn, "test_org", display_name="Test Account")
    add_handle(conn, entity_id, "twitter", "testhandle", is_primary=False)

    adapter = SlopperAdvisoryAdapter()
    with patch("sable_platform.adapters.slopper.get_db", return_value=conn):
        handle = adapter._resolve_primary_handle("test_org")
    assert handle == "@testhandle"


def test_resolve_no_handle_raises(in_memory_db):
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('empty_org', 'Empty')")
    conn.commit()

    adapter = SlopperAdvisoryAdapter()
    with patch("sable_platform.adapters.slopper.get_db", return_value=conn):
        with pytest.raises(SableError) as exc_info:
            adapter._resolve_primary_handle("empty_org")
    assert exc_info.value.code == INVALID_CONFIG


def test_resolve_ignores_archived_entities(in_memory_db):
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('arc_org', 'Arc')")
    conn.commit()
    entity_id = create_entity(conn, "arc_org", display_name="Archived Account")
    add_handle(conn, entity_id, "twitter", "archandle", is_primary=True)
    conn.execute("UPDATE entities SET status='archived' WHERE entity_id=?", (entity_id,))
    conn.commit()

    adapter = SlopperAdvisoryAdapter()
    with patch("sable_platform.adapters.slopper.get_db", return_value=conn):
        with pytest.raises(SableError) as exc_info:
            adapter._resolve_primary_handle("arc_org")
    assert exc_info.value.code == INVALID_CONFIG


def test_resolve_no_double_at_prefix(in_memory_db):
    """Handles stored without @ normalization don't produce @@handle."""
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('at_org', 'At Test')")
    conn.commit()
    entity_id = create_entity(conn, "at_org", display_name="At Account")
    # add_handle normalizes away the @, but verify the adapter output is clean
    add_handle(conn, entity_id, "twitter", "@alreadyat", is_primary=True)

    adapter = SlopperAdvisoryAdapter()
    with patch("sable_platform.adapters.slopper.get_db", return_value=conn):
        handle = adapter._resolve_primary_handle("at_org")
    # add_handle strips the @, so stored as "alreadyat" — adapter prepends one @
    assert handle == "@alreadyat"
    assert not handle.startswith("@@")


def test_run_passes_handle_not_org_id(adapter_db):
    adapter = SlopperAdvisoryAdapter()
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("", "")
    mock_proc.returncode = 0
    mock_proc.pid = 12345

    with patch("sable_platform.adapters.slopper.get_db", return_value=adapter_db), \
         patch.object(adapter, "_repo_path", return_value=Path("/fake/slopper")), \
         patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
        result = adapter.run({"org_id": "solstitch"})

    # Verify the subprocess was called with handle, not org_id
    call_args = mock_popen.call_args[0][0]
    assert "@solstitchxyz" in call_args
    assert "solstitch" not in call_args
    assert result["status"] == "completed"
