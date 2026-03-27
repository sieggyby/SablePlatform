"""Smoke tests for actions CLI commands."""
from __future__ import annotations

import sqlite3

from click.testing import CliRunner

from sable_platform.db.connection import ensure_schema
from sable_platform.db.actions import create_action
from sable_platform.cli.action_cmds import actions_list, actions_create, actions_claim, actions_complete, actions_summary


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


def test_actions_list_empty(monkeypatch):
    conn = _make_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('o1', 'Test', 'active')")
    conn.commit()
    monkeypatch.setattr("sable_platform.cli.action_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(actions_list, ["--org", "o1"])
    assert result.exit_code == 0
    assert "No actions found" in result.output


def test_actions_create_and_list(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    r1 = CliRunner().invoke(actions_create, ["--org", "o1", "--title", "Send DM to alice"])
    assert r1.exit_code == 0
    assert "Created action" in r1.output
    r2 = CliRunner().invoke(actions_list, ["--org", "o1"])
    assert r2.exit_code == 0
    assert "Send DM to alice" in r2.output


def test_actions_claim(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    action_id = create_action(conn, "o1", "Claim me")
    conn.commit()
    conn.close()
    result = CliRunner().invoke(actions_claim, [action_id, "--operator", "alice"])
    assert result.exit_code == 0
    assert "claimed" in result.output.lower()


def test_actions_complete(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    action_id = create_action(conn, "o1", "Complete me")
    conn.execute("UPDATE actions SET status='claimed', operator='alice' WHERE action_id=?", (action_id,))
    conn.commit()
    conn.close()
    result = CliRunner().invoke(actions_complete, [action_id])
    assert result.exit_code == 0
    assert "completed" in result.output.lower()


def test_actions_summary(monkeypatch):
    conn = _make_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('o1', 'Test', 'active')")
    conn.commit()
    monkeypatch.setattr("sable_platform.cli.action_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(actions_summary, ["--org", "o1"])
    assert result.exit_code == 0
    assert "Pending" in result.output
