"""Smoke tests for outcomes CLI commands."""
from __future__ import annotations

import sqlite3

from click.testing import CliRunner

from sable_platform.db.connection import ensure_schema
from sable_platform.cli.outcome_cmds import outcomes_record, outcomes_list, outcomes_diagnostic_delta


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def _setup_file_db(path: str, org_id: str = "o1") -> None:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES (?, 'Test', 'active')", (org_id,))
    conn.commit()
    conn.close()


def test_outcomes_list_empty(monkeypatch):
    conn = _make_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('o1', 'Test', 'active')")
    conn.commit()
    monkeypatch.setattr("sable_platform.cli.outcome_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(outcomes_list, ["--org", "o1"])
    assert result.exit_code == 0
    assert "No outcomes found" in result.output


def test_outcomes_record(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    result = CliRunner().invoke(outcomes_record, ["--org", "o1", "--type", "general"])
    assert result.exit_code == 0
    assert "Recorded outcome" in result.output


def test_diagnostic_delta_no_data(monkeypatch):
    conn = _make_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('o1', 'Test', 'active')")
    conn.commit()
    monkeypatch.setattr("sable_platform.cli.outcome_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(outcomes_diagnostic_delta, ["--org", "o1"])
    assert result.exit_code == 0
    assert "No diagnostic deltas found" in result.output
