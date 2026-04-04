"""Tests for watchlist CLI commands."""
from __future__ import annotations

import json
import sqlite3

from click.testing import CliRunner

from sable_platform.cli.main import cli
from sable_platform.db.connection import ensure_schema


def _setup_db(tmp_path):
    db_path = str(tmp_path / "sable.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('t', 'T', 'active')")
    conn.commit()
    return db_path, conn


def test_watchlist_add_and_list(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["watchlist", "add", "t", "alice", "--note", "watch this one"])
    assert result.exit_code == 0
    assert "Added" in result.output

    result = runner.invoke(cli, ["watchlist", "list", "t"])
    assert result.exit_code == 0
    assert "alice" in result.output


def test_watchlist_remove(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    runner = CliRunner()
    runner.invoke(cli, ["watchlist", "add", "t", "alice"])
    result = runner.invoke(cli, ["watchlist", "remove", "t", "alice"])
    assert result.exit_code == 0
    assert "Removed" in result.output


def test_watchlist_changes_json(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    runner = CliRunner()
    runner.invoke(cli, ["watchlist", "add", "t", "alice"])
    result = runner.invoke(cli, ["watchlist", "changes", "t", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)


def test_watchlist_snapshot(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    runner = CliRunner()
    runner.invoke(cli, ["watchlist", "add", "t", "alice"])
    result = runner.invoke(cli, ["watchlist", "snapshot", "t"])
    assert result.exit_code == 0
    assert "Snapshotted" in result.output
