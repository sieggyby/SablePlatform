"""Smoke tests for journey CLI commands."""
from __future__ import annotations

import sqlite3

from click.testing import CliRunner

from sable_platform.db.connection import ensure_schema
from sable_platform.cli.journey_cmds import journey_show, journey_funnel, journey_first_seen


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


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
