"""Smoke tests for journey CLI commands."""
from __future__ import annotations

import json

from click.testing import CliRunner

from sable_platform.cli.journey_cmds import journey_show, journey_funnel, journey_first_seen, journey_top
from tests.conftest import make_test_conn


def _make_conn():
    return make_test_conn()


def test_journey_show_not_found(monkeypatch):
    conn = _make_conn()
    monkeypatch.setattr("sable_platform.cli.journey_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(journey_show, ["fake_entity_id"])
    assert result.exit_code == 0
    assert "No journey data found" in result.output


def test_journey_funnel_empty_org(monkeypatch):
    conn = _make_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('o1', 'Test', 'active')")
    conn.commit()
    monkeypatch.setattr("sable_platform.cli.journey_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(journey_funnel, ["--org", "o1"])
    assert result.exit_code == 0
    assert "Entity Funnel" in result.output


def test_journey_first_seen_empty(monkeypatch):
    conn = _make_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('o1', 'Test', 'active')")
    conn.commit()
    monkeypatch.setattr("sable_platform.cli.journey_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(journey_first_seen, ["--org", "o1"])
    assert result.exit_code == 0
    assert "No entities found" in result.output


def test_journey_top_empty_org(monkeypatch):
    conn = _make_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('o1', 'Test', 'active')")
    conn.commit()
    monkeypatch.setattr("sable_platform.cli.journey_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(journey_top, ["--org", "o1"])
    assert result.exit_code == 0
    assert "No journey data" in result.output


def test_journey_top_returns_most_events(monkeypatch):
    conn = _make_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('o1', 'Test', 'active')")
    # Insert two entities
    conn.execute(
        "INSERT INTO entities (entity_id, org_id, display_name, status, source) VALUES ('e1', 'o1', 'Alice', 'active', 'manual')"
    )
    conn.execute(
        "INSERT INTO entities (entity_id, org_id, display_name, status, source) VALUES ('e2', 'o1', 'Bob', 'active', 'manual')"
    )
    # Give e1 more events (via actions)
    conn.execute(
        "INSERT INTO actions (action_id, org_id, entity_id, action_type, title, status, created_at) "
        "VALUES ('a1', 'o1', 'e1', 'engage', 'Do thing', 'pending', '2026-01-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO actions (action_id, org_id, entity_id, action_type, title, status, created_at) "
        "VALUES ('a2', 'o1', 'e1', 'engage', 'Do more', 'pending', '2026-01-02 00:00:00')"
    )
    conn.commit()
    monkeypatch.setattr("sable_platform.cli.journey_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(journey_top, ["--org", "o1", "--limit", "2"])
    assert result.exit_code == 0
    # Alice has more events — should appear first
    assert "Alice" in result.output


def test_journey_top_json(monkeypatch):
    conn = _make_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('o1', 'Test', 'active')")
    conn.execute(
        "INSERT INTO entities (entity_id, org_id, display_name, status, source) VALUES ('e1', 'o1', 'Alice', 'active', 'manual')"
    )
    conn.commit()
    monkeypatch.setattr("sable_platform.cli.journey_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(journey_top, ["--org", "o1", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["entity_id"] == "e1"
