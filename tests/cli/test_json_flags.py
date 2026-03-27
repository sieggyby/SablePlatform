"""Tests for --json flag on list/status commands."""
from __future__ import annotations

import json
import sqlite3
import uuid

from click.testing import CliRunner

from sable_platform.db.connection import ensure_schema
from sable_platform.cli.workflow_cmds import workflow_list, workflow_status
from sable_platform.cli.alert_cmds import alerts_list
from sable_platform.cli.org_cmds import org_list
from sable_platform.cli.action_cmds import actions_list


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def _insert_org(conn, org_id="o1"):
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES (?, 'Test Org', 'active')", (org_id,))
    conn.commit()


def _insert_run(conn, org_id="o1", run_id=None, status="completed"):
    run_id = run_id or uuid.uuid4().hex
    conn.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, config_json) "
        "VALUES (?, ?, 'test_wf', ?, '{}')",
        (run_id, org_id, status),
    )
    conn.commit()
    return run_id


def test_workflow_list_json(monkeypatch):
    conn = _make_conn()
    _insert_org(conn)
    _insert_run(conn)
    _insert_run(conn)
    monkeypatch.setattr("sable_platform.cli.workflow_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(workflow_list, ["--org", "o1", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 2
    assert "run_id" in data[0]


def test_workflow_list_json_empty(monkeypatch):
    conn = _make_conn()
    _insert_org(conn)
    monkeypatch.setattr("sable_platform.cli.workflow_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(workflow_list, ["--org", "o1", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == []


def test_workflow_status_json(monkeypatch):
    conn = _make_conn()
    _insert_org(conn)
    run_id = _insert_run(conn)
    conn.execute(
        "INSERT INTO workflow_steps (step_id, run_id, step_name, step_index, status, retries) "
        "VALUES (?, ?, 'step_a', 0, 'completed', 0)",
        (uuid.uuid4().hex, run_id),
    )
    conn.execute(
        "INSERT INTO workflow_steps (step_id, run_id, step_name, step_index, status, retries) "
        "VALUES (?, ?, 'step_b', 1, 'completed', 0)",
        (uuid.uuid4().hex, run_id),
    )
    conn.commit()
    monkeypatch.setattr("sable_platform.cli.workflow_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(workflow_status, [run_id, "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "run" in data
    assert "steps" in data
    assert len(data["steps"]) == 2


def test_workflow_status_json_not_found(monkeypatch):
    conn = _make_conn()
    monkeypatch.setattr("sable_platform.cli.workflow_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(workflow_status, ["nonexistent_run", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "error" in data


def test_alerts_list_json(monkeypatch):
    conn = _make_conn()
    _insert_org(conn)
    for i in range(3):
        conn.execute(
            "INSERT INTO alerts (alert_id, org_id, alert_type, severity, title, status, dedup_key) "
            "VALUES (?, 'o1', 'tracking_stale', 'critical', 'Test', 'new', ?)",
            (uuid.uuid4().hex, f"key_{i}"),
        )
    conn.commit()
    monkeypatch.setattr("sable_platform.cli.alert_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(alerts_list, ["--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 3
    assert "alert_id" in data[0]


def test_org_list_json(monkeypatch):
    conn = _make_conn()
    _insert_org(conn, "org_a")
    _insert_org(conn, "org_b")
    monkeypatch.setattr("sable_platform.cli.org_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(org_list, ["--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 2
    assert "org_id" in data[0]


def test_actions_list_json(monkeypatch):
    conn = _make_conn()
    _insert_org(conn)
    from sable_platform.db.actions import create_action
    create_action(conn, "o1", "Action One")
    create_action(conn, "o1", "Action Two")
    monkeypatch.setattr("sable_platform.cli.action_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(actions_list, ["--org", "o1", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 2
    assert "action_id" in data[0]
