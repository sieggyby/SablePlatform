"""Smoke tests for org CLI commands."""
from __future__ import annotations

import sqlite3

from click.testing import CliRunner

from sable_platform.db.connection import ensure_schema
from sable_platform.cli.org_cmds import org_list, org_create


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def _setup_file_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    conn.commit()
    conn.close()


def test_org_list_empty(monkeypatch):
    conn = _make_conn()
    monkeypatch.setattr("sable_platform.cli.org_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(org_list, [])
    assert result.exit_code == 0
    assert "No orgs found" in result.output


def test_org_create_and_list(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    r1 = CliRunner().invoke(org_create, ["myorg", "--name", "My Org"])
    assert r1.exit_code == 0
    assert "myorg" in r1.output
    r2 = CliRunner().invoke(org_list, [])
    assert r2.exit_code == 0
    assert "myorg" in r2.output


def test_org_create_duplicate(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    CliRunner().invoke(org_create, ["duporg", "--name", "Dup"])
    r2 = CliRunner().invoke(org_create, ["duporg", "--name", "Dup"])
    assert r2.exit_code != 0
    assert "already exists" in r2.output
