"""Tests for workflow preflight CLI command."""
from __future__ import annotations

import json
import sqlite3

from click.testing import CliRunner

from sable_platform.cli.main import cli
from sable_platform.db.connection import ensure_schema
from sable_platform.db.alerts import create_alert
from sable_platform.db.cost import log_cost


def _setup_db(tmp_path):
    db_path = str(tmp_path / "sable.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return db_path, conn


def test_preflight_healthy_org(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('t', 'T', 'active')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["workflow", "preflight", "--org", "t"])
    assert result.exit_code == 0
    assert "OK" in result.output


def test_preflight_stuck_run(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('t', 'T', 'active')")
    conn.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, config_json, started_at) "
        "VALUES ('r1', 't', 'test_wf', 'running', '{}', datetime('now', '-3 hours'))"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["workflow", "preflight", "--org", "t"])
    assert result.exit_code == 1
    assert "stuck" in result.output.lower()


def test_preflight_budget_exceeded(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('t', 'T', 'active')")
    conn.commit()
    # Default cap is $5.00; spend $4.60 (92%)
    log_cost(conn, "t", "llm_call", 4.60)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["workflow", "preflight", "--org", "t"])
    assert result.exit_code == 1
    assert "budget" in result.output.lower()


def test_preflight_critical_alert(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('t', 'T', 'active')")
    conn.commit()
    create_alert(conn, "test", "critical", "Critical!", org_id="t", dedup_key="c1")
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["workflow", "preflight", "--org", "t"])
    assert result.exit_code == 1
    assert "critical" in result.output.lower()


def test_preflight_missing_org(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["workflow", "preflight", "--org", "nonexistent"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_preflight_all_orgs(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('good', 'G', 'active')")
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('bad', 'B', 'active')")
    conn.commit()
    create_alert(conn, "test", "critical", "Critical!", org_id="bad", dedup_key="c1")
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["workflow", "preflight"])
    assert result.exit_code == 1
    assert "good" in result.output
    assert "bad" in result.output


def test_preflight_all_orgs_healthy(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('a', 'A', 'active')")
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('b', 'B', 'active')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["workflow", "preflight"])
    assert result.exit_code == 0
    assert "OK" in result.output
