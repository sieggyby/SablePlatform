"""Tests for inspect audit CLI command."""
from __future__ import annotations

import json
import sqlite3

from click.testing import CliRunner

from sable_platform.cli.main import cli
from sable_platform.db.connection import ensure_schema
from sable_platform.db.audit import log_audit


def _setup_db(tmp_path):
    db_path = str(tmp_path / "sable.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return db_path, conn


def test_inspect_audit_empty(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "audit"])
    assert result.exit_code == 0
    assert "No audit entries" in result.output


def test_inspect_audit_with_entries(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    log_audit(conn, "cli:alice", "alert_acknowledge", org_id="org1")
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "audit"])
    assert result.exit_code == 0
    assert "cli:alice" in result.output
    assert "alert_acknowledge" in result.output


def test_inspect_audit_json(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    log_audit(conn, "cli:alice", "alert_acknowledge", org_id="org1")
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "audit", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["actor"] == "cli:alice"
