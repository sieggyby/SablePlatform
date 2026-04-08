"""Tests for 'sable-platform inspect interactions' CLI command."""
from __future__ import annotations

import json

from click.testing import CliRunner

from sable_platform.db.interactions import sync_interaction_edges
from sable_platform.cli.inspect_cmds import inspect_interactions
from tests.conftest import make_test_conn


def _seed(conn):
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('acme', 'Acme', 'active')")
    conn.commit()
    edges = [
        {"source_handle": "alice", "target_handle": "bob", "interaction_type": "reply", "count": 5, "last_seen": "2026-03-20"},
        {"source_handle": "carol", "target_handle": "alice", "interaction_type": "mention", "count": 2, "last_seen": "2026-03-18"},
    ]
    sync_interaction_edges(conn, "acme", edges, "2026-03-20")


def test_inspect_interactions_table_output(monkeypatch):
    conn = make_test_conn()
    _seed(conn)
    monkeypatch.setattr("sable_platform.cli.inspect_cmds.get_db", lambda: conn)

    result = CliRunner().invoke(inspect_interactions, ["acme"])
    assert result.exit_code == 0
    assert "alice" in result.output
    assert "bob" in result.output
    assert "reply" in result.output


def test_inspect_interactions_json_output(monkeypatch):
    conn = make_test_conn()
    _seed(conn)
    monkeypatch.setattr("sable_platform.cli.inspect_cmds.get_db", lambda: conn)

    result = CliRunner().invoke(inspect_interactions, ["acme", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2
    assert data[0]["count"] == 5  # highest count first


def test_inspect_interactions_type_filter(monkeypatch):
    conn = make_test_conn()
    _seed(conn)
    monkeypatch.setattr("sable_platform.cli.inspect_cmds.get_db", lambda: conn)

    result = CliRunner().invoke(inspect_interactions, ["acme", "--type", "mention"])
    assert result.exit_code == 0
    assert "mention" in result.output
    assert "reply" not in result.output


def test_inspect_interactions_min_count(monkeypatch):
    conn = make_test_conn()
    _seed(conn)
    monkeypatch.setattr("sable_platform.cli.inspect_cmds.get_db", lambda: conn)

    result = CliRunner().invoke(inspect_interactions, ["acme", "--min-count", "3"])
    assert result.exit_code == 0
    assert "alice" in result.output
    assert "carol" not in result.output


def test_inspect_interactions_empty_org(monkeypatch):
    conn = make_test_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('empty', 'E', 'active')")
    conn.commit()
    monkeypatch.setattr("sable_platform.cli.inspect_cmds.get_db", lambda: conn)

    result = CliRunner().invoke(inspect_interactions, ["empty"])
    assert result.exit_code == 0
    assert "No interactions found" in result.output
