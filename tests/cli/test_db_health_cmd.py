"""Tests for the backend-neutral `sable-platform db-health` command."""
from __future__ import annotations

import json
import sqlite3

from click.testing import CliRunner

from sable_platform.cli.main import cli


def _create_test_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_version (version INTEGER)")
    conn.execute("INSERT INTO schema_version VALUES (30)")
    conn.execute("CREATE TABLE orgs (org_id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE diagnostic_runs (run_id INTEGER PRIMARY KEY, started_at TEXT)")
    conn.execute("CREATE TABLE platform_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()


def test_db_health_reports_healthy_sqlite(tmp_path, monkeypatch):
    db_path = str(tmp_path / "sable.db")
    _create_test_db(db_path)
    monkeypatch.delenv("SABLE_OPERATOR_ID", raising=False)

    result = CliRunner().invoke(cli, ["db-health", "--db-path", db_path, "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["migration_version"] == 30


def test_db_health_fails_for_missing_sqlite_file(tmp_path, monkeypatch):
    db_path = str(tmp_path / "missing.db")
    monkeypatch.delenv("SABLE_OPERATOR_ID", raising=False)

    result = CliRunner().invoke(cli, ["db-health", "--db-path", db_path])

    assert result.exit_code == 1
    assert "Database not found" in result.output
