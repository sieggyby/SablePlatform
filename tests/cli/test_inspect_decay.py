"""Tests for 'sable-platform inspect decay' CLI command."""
from __future__ import annotations

import json
import sqlite3

from click.testing import CliRunner

from sable_platform.db.connection import ensure_schema
from sable_platform.db.decay import sync_decay_scores
from sable_platform.cli.inspect_cmds import inspect_decay


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def _seed(conn):
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('acme', 'Acme', 'active')")
    conn.commit()
    sync_decay_scores(conn, "acme", [
        {"handle": "alice", "decay_score": 0.85, "risk_tier": "critical",
         "factors": {"activity_drop": 0.5, "sentiment_drift": 0.2}},
        {"handle": "bob", "decay_score": 0.55, "risk_tier": "medium"},
        {"handle": "carol", "decay_score": 0.3, "risk_tier": "low"},
    ], "2026-04-01")


def test_inspect_decay_table_output(monkeypatch):
    conn = _make_conn()
    _seed(conn)
    monkeypatch.setattr("sable_platform.cli.inspect_cmds.get_db", lambda: conn)

    result = CliRunner().invoke(inspect_decay, ["acme"])
    assert result.exit_code == 0
    assert "alice" in result.output
    assert "critical" in result.output
    # Default min_score=0.5 — carol (0.3) should be excluded
    assert "carol" not in result.output


def test_inspect_decay_json_output(monkeypatch):
    conn = _make_conn()
    _seed(conn)
    monkeypatch.setattr("sable_platform.cli.inspect_cmds.get_db", lambda: conn)

    result = CliRunner().invoke(inspect_decay, ["acme", "--json", "--min-score", "0"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 3
    assert data[0]["decay_score"] == 0.85  # highest first
    assert "factors_json" in data[0]


def test_inspect_decay_min_score_filter(monkeypatch):
    conn = _make_conn()
    _seed(conn)
    monkeypatch.setattr("sable_platform.cli.inspect_cmds.get_db", lambda: conn)

    result = CliRunner().invoke(inspect_decay, ["acme", "--min-score", "0.8"])
    assert result.exit_code == 0
    assert "alice" in result.output
    assert "bob" not in result.output


def test_inspect_decay_tier_filter(monkeypatch):
    conn = _make_conn()
    _seed(conn)
    monkeypatch.setattr("sable_platform.cli.inspect_cmds.get_db", lambda: conn)

    result = CliRunner().invoke(inspect_decay, ["acme", "--tier", "critical", "--min-score", "0"])
    assert result.exit_code == 0
    assert "alice" in result.output
    assert "bob" not in result.output


def test_inspect_decay_empty(monkeypatch):
    conn = _make_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('empty', 'E', 'active')")
    conn.commit()
    monkeypatch.setattr("sable_platform.cli.inspect_cmds.get_db", lambda: conn)

    result = CliRunner().invoke(inspect_decay, ["empty"])
    assert result.exit_code == 0
    assert "No decay scores" in result.output


def test_inspect_decay_factors_display(monkeypatch):
    conn = _make_conn()
    _seed(conn)
    monkeypatch.setattr("sable_platform.cli.inspect_cmds.get_db", lambda: conn)

    result = CliRunner().invoke(inspect_decay, ["acme", "--min-score", "0.8"])
    assert result.exit_code == 0
    assert "activity_drop" in result.output
