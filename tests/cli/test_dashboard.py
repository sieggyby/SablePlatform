"""Tests for dashboard CLI command."""
from __future__ import annotations

import json

from click.testing import CliRunner

from sable_platform.cli.main import cli
from sable_platform.db.alerts import create_alert
from tests.conftest import make_test_file_db


def _setup_db(tmp_path):
    db_path = str(tmp_path / "sable.db")
    conn = make_test_file_db(db_path)
    return db_path, conn


def test_dashboard_empty_db(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["dashboard"])
    assert result.exit_code == 0
    assert "No active orgs" in result.output


def test_dashboard_single_org(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('org1', 'Org 1', 'active')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["dashboard"])
    assert result.exit_code == 0
    assert "org1" in result.output


def test_dashboard_json_output(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('org1', 'Org 1', 'active')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["dashboard", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["org_id"] == "org1"


def test_dashboard_org_filter(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('org1', 'Org 1', 'active')")
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('org2', 'Org 2', 'active')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["dashboard", "--org", "org1"])
    assert result.exit_code == 0
    assert "org1" in result.output
    assert "org2" not in result.output


def test_dashboard_urgency_sort(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('org_warn', 'W', 'active')")
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('org_crit', 'C', 'active')")
    conn.commit()

    # Only warnings for org_warn
    create_alert(conn, "test", "warning", "warn", org_id="org_warn", dedup_key="w1")
    # Critical for org_crit
    create_alert(conn, "test", "critical", "crit", org_id="org_crit", dedup_key="c1")
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["dashboard", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    # Critical org should sort first
    assert data[0]["org_id"] == "org_crit"
