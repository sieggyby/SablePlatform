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

    def test_compat_connection_returned(self, tmp_path):
        """get_db() now returns a CompatConnection wrapping SA."""
        from sable_platform.db.compat_conn import CompatConnection
        db_path = tmp_path / "rf.db"
        conn = get_db(db_path)
        try:
            assert isinstance(conn, CompatConnection)
        finally:
            conn.close()

    def test_busy_timeout_set(self, tmp_path):
        db_path = tmp_path / "bt.db"
        conn = get_db(db_path)
        try:
            bt = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            assert bt == 5000
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
    def test_idempotent(self):
        """Running ensure_schema twice should not raise or duplicate data."""
        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys=ON")
        ensure_schema(raw)
        ensure_schema(raw)
        row = raw.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] >= 27  # all migrations applied
        raw.close()

    def test_applies_all_migrations(self):
        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row
        ensure_schema(raw)
        row = raw.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] == 51
        raw.close()

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

    def test_performance_indexes_exist(self, in_memory_db):
        """Migration 030 creates performance indexes."""
        indexes = {
            r[0] for r in in_memory_db.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_entity_tags_current" in indexes
        assert "idx_cost_events_org_date" in indexes
