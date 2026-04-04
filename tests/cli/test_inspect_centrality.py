"""Tests for inspect centrality CLI command."""
from __future__ import annotations

import json
import sqlite3

from click.testing import CliRunner

from sable_platform.cli.main import cli
from sable_platform.db.connection import ensure_schema
from sable_platform.db.centrality import sync_centrality_scores


def _setup_db(tmp_path):
    db_path = str(tmp_path / "sable.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('t', 'T', 'active')")
    conn.commit()
    return db_path, conn


def test_inspect_centrality_empty(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "centrality", "t"])
    assert result.exit_code == 0
    assert "No centrality scores" in result.output


def test_inspect_centrality_with_data(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    sync_centrality_scores(conn, "t", [
        {"handle": "alice", "in_centrality": 0.6, "out_centrality": 0.4},
    ], "2026-04-01")
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "centrality", "t"])
    assert result.exit_code == 0
    assert "alice" in result.output


def test_inspect_centrality_json(tmp_path, monkeypatch):
    db_path, conn = _setup_db(tmp_path)
    sync_centrality_scores(conn, "t", [
        {"handle": "alice", "in_centrality": 0.6, "out_centrality": 0.4},
    ], "2026-04-01")
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)

    result = CliRunner().invoke(cli, ["inspect", "centrality", "t", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["entity_id"] == "alice"
    assert data[0]["in_centrality"] == 0.6
