"""Tests for db/allowlist.py (migration 075 DB-backed allowlist CRUD)."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from sable_platform.db import allowlist as al


def test_upsert_lowercases_email_and_round_trips_assigned_orgs(in_memory_db):
    al.upsert_entry(in_memory_db, "OP@Sable.IO", "operator", operator_id="op1",
                    assigned_orgs=["tig", "solstitch"])
    got = al.get_entry(in_memory_db, "op@sable.io")
    assert got["email"] == "op@sable.io"  # lowercased
    assert got["role"] == "operator" and got["operator_id"] == "op1"
    assert got["assigned_orgs"] == ["tig", "solstitch"]  # JSON round-trip
    # lookup is case-insensitive (mirrors SableWeb)
    assert al.get_entry(in_memory_db, "OP@SABLE.IO")["email"] == "op@sable.io"


def test_upsert_validates_role_and_required_fields(in_memory_db):
    with pytest.raises(ValueError):
        al.upsert_entry(in_memory_db, "x@y.io", "wizard")  # bad role
    with pytest.raises(ValueError):
        al.upsert_entry(in_memory_db, "x@y.io", "client")  # client needs org
    with pytest.raises(ValueError):
        al.upsert_entry(in_memory_db, "x@y.io", "operator")  # operator needs operator_id


def test_upsert_is_idempotent_update(in_memory_db):
    al.upsert_entry(in_memory_db, "c@y.io", "client", org="tig")
    al.upsert_entry(in_memory_db, "c@y.io", "client", org="solstitch")  # update org
    assert al.get_entry(in_memory_db, "c@y.io")["org"] == "solstitch"
    assert len(al.list_entries(in_memory_db)) == 1  # PK upsert, no dup


def test_enabled_only_filter_and_set_enabled(in_memory_db):
    al.upsert_entry(in_memory_db, "a@y.io", "admin", operator_id="a")
    al.upsert_entry(in_memory_db, "b@y.io", "admin", operator_id="b", enabled=False)
    assert {r["email"] for r in al.list_entries(in_memory_db, enabled_only=True)} == {"a@y.io"}
    assert al.set_enabled(in_memory_db, "b@y.io", True) == 1
    assert len(al.list_entries(in_memory_db, enabled_only=True)) == 2
    assert al.set_enabled(in_memory_db, "ghost@y.io", False) == 0  # no such entry


def test_remove_entry(in_memory_db):
    al.upsert_entry(in_memory_db, "a@y.io", "admin", operator_id="a")
    assert al.remove_entry(in_memory_db, "A@Y.io") == 1  # case-insensitive
    assert al.get_entry(in_memory_db, "a@y.io") is None
    assert al.remove_entry(in_memory_db, "a@y.io") == 0


def test_db_check_constraints_defense_in_depth(in_memory_db):
    # the DB CHECKs reject a bad role + a non-lowercased email even if the helper is bypassed
    with pytest.raises(IntegrityError):
        in_memory_db.execute(text(
            "INSERT INTO allowlist_entries (email, role) VALUES ('x@y.io', 'wizard')"
        ))
    in_memory_db.rollback()
    with pytest.raises(IntegrityError):
        in_memory_db.execute(text(
            "INSERT INTO allowlist_entries (email, role) VALUES ('MIXED@y.io', 'admin')"
        ))
