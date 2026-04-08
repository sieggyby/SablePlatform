"""Tests for inspect playbook CLI command."""
from __future__ import annotations

import json

from click.testing import CliRunner

from sable_platform.cli.main import cli
from sable_platform.db.playbook import (
    upsert_playbook_targets,
    record_playbook_outcomes,
)
from tests.conftest import make_test_file_db


def _setup_db(tmp_path):
    db_path = str(tmp_path / "sable.db")
    conn = make_test_file_db(db_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('t', 'T', 'active')")
    conn.commit()
    return db_path, conn


def test_inspect_playbook_targets_empty(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "playbook", "t"])
    assert result.exit_code == 0
    assert "No playbook targets" in result.output


def test_inspect_playbook_outcomes_empty(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "playbook", "t", "--outcomes"])
    assert result.exit_code == 0
    assert "No playbook outcomes" in result.output


def test_inspect_playbook_targets_with_data(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    upsert_playbook_targets(conn, "t", [
        {"metric": "retention", "target": 0.75},
        {"metric": "echo_rate", "target": 0.3},
    ], artifact_id="art_1")
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "playbook", "t"])
    assert result.exit_code == 0
    assert "2 items" in result.output
    assert "art_1" in result.output


def test_inspect_playbook_outcomes_with_data(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    record_playbook_outcomes(conn, "t", {
        "retention": {"target": 0.75, "actual": 0.80, "met": True},
    }, targets_artifact_id="art_1")
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "playbook", "t", "--outcomes"])
    assert result.exit_code == 0
    assert "retention" in result.output


def test_inspect_playbook_targets_json(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    upsert_playbook_targets(conn, "t", [{"metric": "retention", "target": 0.75}])
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "playbook", "t", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["org_id"] == "t"


def test_inspect_playbook_outcomes_json(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    record_playbook_outcomes(conn, "t", {"echo_rate": {"met": False}})
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "playbook", "t", "--outcomes", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert "outcomes_json" in data[0]


def test_inspect_playbook_limit(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    for i in range(5):
        upsert_playbook_targets(conn, "t", [{"batch": i}])
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "playbook", "t", "--limit", "2", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2
