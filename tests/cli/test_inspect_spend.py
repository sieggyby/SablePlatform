"""Tests for inspect spend CLI command."""
from __future__ import annotations

import json

from click.testing import CliRunner

from sable_platform.cli.main import cli
from sable_platform.db.cost import log_cost
from tests.conftest import make_test_file_db


def _setup_db(tmp_path):
    db_path = str(tmp_path / "sable.db")
    conn = make_test_file_db(db_path)
    return db_path, conn


def test_spend_no_cost_events(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('t', 'T', 'active')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "spend", "--org", "t"])
    assert result.exit_code == 0
    assert "$0.00" in result.output or "0.00" in result.output


def test_spend_with_cost_events(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('t', 'T', 'active')")
    conn.commit()
    log_cost(conn, "t", "llm_call", 1.50, model="gpt-4")
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "spend", "--org", "t"])
    assert result.exit_code == 0
    assert "1.50" in result.output


def test_spend_json(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('t', 'T', 'active')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "spend", "--org", "t", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["org_id"] == "t"


def test_spend_all_orgs(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('a', 'A', 'active')")
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('b', 'B', 'active')")
    conn.commit()
    log_cost(conn, "a", "llm_call", 2.00)
    log_cost(conn, "b", "llm_call", 1.00)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "spend", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2
    # Sorted by pct_used descending
    assert data[0]["weekly_spend_usd"] >= data[1]["weekly_spend_usd"]
