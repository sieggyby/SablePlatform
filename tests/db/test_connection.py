"""Tests for sable_platform.db.connection module."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sable_platform.db.connection import ensure_schema, get_db, sable_db_path


# ---------------------------------------------------------------------------
# sable_db_path
# ---------------------------------------------------------------------------

class TestSableDbPath:
    def test_default_path(self, monkeypatch):
        monkeypatch.delenv("SABLE_DB_PATH", raising=False)
        assert sable_db_path() == Path.home() / ".sable" / "sable.db"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SABLE_DB_PATH", "/tmp/custom.db")
        assert sable_db_path() == Path("/tmp/custom.db")


# ---------------------------------------------------------------------------
# get_db
# ---------------------------------------------------------------------------

class TestGetDb:
    def test_creates_file_and_schema(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = get_db(db_path)
        try:
            assert db_path.exists()
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            assert row[0] >= 1
        finally:
            conn.close()

    def test_wal_mode(self, tmp_path):
        db_path = tmp_path / "wal.db"
        conn = get_db(db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"
        finally:
            conn.close()

    def test_foreign_keys_enabled(self, tmp_path):
        db_path = tmp_path / "fk.db"
        conn = get_db(db_path)
        try:
            fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert fk == 1
        finally:
            conn.close()

    def test_row_factory_set(self, tmp_path):
        db_path = tmp_path / "rf.db"
        conn = get_db(db_path)
        try:
            assert conn.row_factory is sqlite3.Row
        finally:
            conn.close()

    def test_creates_parent_dirs(self, tmp_path):
        db_path = tmp_path / "deep" / "nested" / "test.db"
        conn = get_db(db_path)
        try:
            assert db_path.exists()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# ensure_schema
# ---------------------------------------------------------------------------

class TestEnsureSchema:
    def test_idempotent(self, in_memory_db):
        """Running ensure_schema twice should not raise or duplicate data."""
        ensure_schema(in_memory_db)
        row = in_memory_db.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] >= 22  # all 22 migrations applied

    def test_applies_all_migrations(self, in_memory_db):
        row = in_memory_db.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] == 22

    def test_tables_exist(self, in_memory_db):
        """Spot-check that key tables were created."""
        tables = {
            r[0] for r in in_memory_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {
            "orgs", "entities", "entity_handles", "entity_tags",
            "jobs", "job_steps", "artifacts", "actions", "alerts",
            "workflow_runs", "workflow_steps", "audit_log",
        }
        assert expected.issubset(tables)
