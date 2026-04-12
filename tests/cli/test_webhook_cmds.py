"""Tests for webhook CLI commands."""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

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


def test_webhooks_test_targets_requested_subscription_only(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    runner = CliRunner()
    runner.invoke(cli, [
        "webhooks", "add", "t",
        "--url", "https://example.com/hook-a",
        "--events", "alert.created",
        "--secret", "a" * 32,
    ])
    runner.invoke(cli, [
        "webhooks", "add", "t",
        "--url", "https://example.com/hook-b",
        "--events", "workflow.completed",
        "--secret", "b" * 32,
    ])

    with patch("sable_platform.webhooks.dispatch.dispatch_event", return_value=1) as mock_dispatch:
        result = runner.invoke(cli, ["webhooks", "test", "t", "2"])

    assert result.exit_code == 0
    mock_dispatch.assert_called_once()
    assert mock_dispatch.call_args.kwargs["subscription_ids"] == [2]
    assert mock_dispatch.call_args.kwargs["bypass_event_filters"] is True


def test_webhooks_test_rejects_subscription_for_other_org(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, status) VALUES ('other', 'Other', 'active')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    runner = CliRunner()
    runner.invoke(cli, [
        "webhooks", "add", "other",
        "--url", "https://example.com/hook",
        "--events", "alert.created",
        "--secret", "a" * 32,
    ])

    result = runner.invoke(cli, ["webhooks", "test", "t", "1"])

    assert result.exit_code == 1
    assert "belongs to org 'other'" in result.output


def test_webhooks_test_rejects_missing_subscription(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["webhooks", "test", "t", "999"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()
