"""Smoke tests for workflow CLI commands."""
from __future__ import annotations

import sqlite3
import uuid

from click.testing import CliRunner

from sable_platform.db.connection import ensure_schema
from sable_platform.cli.workflow_cmds import workflow_list, workflow_events, workflow_status, workflow_gc


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def _insert_org(conn, org_id="o1"):
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES (?, 'Test', 'active')", (org_id,))
    conn.commit()


def _insert_run(conn, org_id="o1", status="completed"):
    run_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, config_json) "
        "VALUES (?, ?, 'test_wf', ?, '{}')",
        (run_id, org_id, status),
    )
    conn.commit()
    return run_id


def test_workflow_list_empty(monkeypatch):
    conn = _make_conn()
    _insert_org(conn)
    monkeypatch.setattr("sable_platform.cli.workflow_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(workflow_list, ["--org", "o1"])
    assert result.exit_code == 0
    assert "No runs found" in result.output


def test_workflow_list_has_rows(monkeypatch):
    conn = _make_conn()
    _insert_org(conn)
    run_id = _insert_run(conn)
    monkeypatch.setattr("sable_platform.cli.workflow_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(workflow_list, ["--org", "o1"])
    assert result.exit_code == 0
    assert run_id[:8] in result.output


def test_workflow_gc_no_stuck(monkeypatch):
    conn = _make_conn()
    _insert_org(conn)
    monkeypatch.setattr("sable_platform.cli.workflow_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(workflow_gc, ["--hours", "2"])
    assert result.exit_code == 0
    assert "Marked 0" in result.output


def test_workflow_events_not_found(monkeypatch):
    conn = _make_conn()
    monkeypatch.setattr("sable_platform.cli.workflow_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(workflow_events, ["fake_run_id"])
    assert result.exit_code == 0
    assert "No events found" in result.output


def test_workflow_status_not_found(monkeypatch):
    conn = _make_conn()
    monkeypatch.setattr("sable_platform.cli.workflow_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(workflow_status, ["fake_run_id"])
    assert result.exit_code == 0
    assert "not found" in result.output
