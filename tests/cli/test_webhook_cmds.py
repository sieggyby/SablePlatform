"""Tests for webhook CLI commands."""
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


def test_webhooks_add_and_list(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "webhooks", "add", "t",
        "--url", "https://example.com/hook",
        "--events", "alert.created,workflow.completed",
        "--secret", "a" * 32,
    ])
    assert result.exit_code == 0
    assert "Created" in result.output

    result = runner.invoke(cli, ["webhooks", "list", "t"])
    assert result.exit_code == 0
    assert "example.com" in result.output


def test_webhooks_remove(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    runner = CliRunner()
    runner.invoke(cli, [
        "webhooks", "add", "t",
        "--url", "https://example.com/hook",
        "--events", "alert.created",
        "--secret", "a" * 32,
    ])
    result = runner.invoke(cli, ["webhooks", "remove", "1"])
    assert result.exit_code == 0
    assert "Deleted" in result.output


def test_webhooks_list_json(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    runner = CliRunner()
    runner.invoke(cli, [
        "webhooks", "add", "t",
        "--url", "https://example.com/hook",
        "--events", "alert.created",
        "--secret", "a" * 32,
    ])
    result = runner.invoke(cli, ["webhooks", "list", "t", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1
    assert "****" in data[0]["secret"]
