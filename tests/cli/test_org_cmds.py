"""Smoke tests for org CLI commands."""
from __future__ import annotations

import json
import sqlite3

from click.testing import CliRunner

from sable_platform.db.connection import ensure_schema
from sable_platform.cli.org_cmds import org_list, org_create, org_reject, org_config_set, org_config_get, org_config_list


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def _setup_file_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    conn.commit()
    conn.close()


def test_org_list_empty(monkeypatch):
    conn = _make_conn()
    monkeypatch.setattr("sable_platform.cli.org_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(org_list, [])
    assert result.exit_code == 0
    assert "No orgs found" in result.output


def test_org_create_and_list(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    r1 = CliRunner().invoke(org_create, ["myorg", "--name", "My Org"])
    assert r1.exit_code == 0
    assert "myorg" in r1.output
    r2 = CliRunner().invoke(org_list, [])
    assert r2.exit_code == 0
    assert "myorg" in r2.output


def test_org_create_duplicate(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    CliRunner().invoke(org_create, ["duporg", "--name", "Dup"])
    r2 = CliRunner().invoke(org_create, ["duporg", "--name", "Dup"])
    assert r2.exit_code != 0
    assert "already exists" in r2.output


def test_org_reject_success(tmp_path, monkeypatch):
    """CLI org reject stamps rejected_at and prints confirmation."""
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    # Seed a prospect
    import sqlite3 as _sql
    conn = _sql.connect(db_path)
    conn.row_factory = _sql.Row
    from sable_platform.db.prospects import sync_prospect_scores
    sync_prospect_scores(conn, [{"org_id": "proj_x", "composite_score": 0.8, "tier": "Tier 1"}], "2026-04-04")
    conn.close()

    result = CliRunner().invoke(org_reject, ["proj_x", "--reason", "bad fit"])
    assert result.exit_code == 0
    assert "Rejected" in result.output
    assert "proj_x" in result.output


def test_org_reject_nonexistent(tmp_path, monkeypatch):
    """CLI org reject exits 1 for nonexistent prospect."""
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    result = CliRunner().invoke(org_reject, ["no_such_project"])
    assert result.exit_code != 0
    assert "No prospect scores found" in result.output


def test_org_config_set_and_get(tmp_path, monkeypatch):
    """org config set/get round-trips a key."""
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    CliRunner().invoke(org_create, ["cfgorg", "--name", "Config Org"])
    r = CliRunner().invoke(org_config_set, ["cfgorg", "sector", "DeFi"])
    assert r.exit_code == 0
    assert "DeFi" in r.output
    r2 = CliRunner().invoke(org_config_get, ["cfgorg", "sector"])
    assert r2.exit_code == 0
    assert "DeFi" in r2.output


def test_org_config_set_invalid_sector(tmp_path, monkeypatch):
    """org config set rejects an unknown sector."""
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    CliRunner().invoke(org_create, ["cfgorg2", "--name", "X"])
    r = CliRunner().invoke(org_config_set, ["cfgorg2", "sector", "NotReal"])
    assert r.exit_code != 0
    assert "Invalid sector" in r.output


def test_org_config_set_invalid_stage(tmp_path, monkeypatch):
    """org config set rejects an unknown stage."""
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    CliRunner().invoke(org_create, ["cfgorg3", "--name", "Y"])
    r = CliRunner().invoke(org_config_set, ["cfgorg3", "stage", "unicorn"])
    assert r.exit_code != 0
    assert "Invalid stage" in r.output


def test_org_config_list(tmp_path, monkeypatch):
    """org config list shows all orgs including those with empty config."""
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    CliRunner().invoke(org_create, ["listorg", "--name", "List Org"])
    CliRunner().invoke(org_config_set, ["listorg", "stage", "growth"])
    r = CliRunner().invoke(org_config_list, [])
    assert r.exit_code == 0
    assert "listorg" in r.output
    assert "growth" in r.output


def test_org_config_get_unknown_key(tmp_path, monkeypatch):
    """org config get prints a message for missing key without exiting 1."""
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    CliRunner().invoke(org_create, ["emptyorg", "--name", "Empty"])
    r = CliRunner().invoke(org_config_get, ["emptyorg", "sector"])
    assert r.exit_code == 0
    assert "not set" in r.output


def test_org_config_numeric_key(tmp_path, monkeypatch):
    """org config set coerces numeric keys to float."""
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    CliRunner().invoke(org_create, ["numorg", "--name", "Num"])
    r = CliRunner().invoke(org_config_set, ["numorg", "max_ai_usd_per_org_per_week", "7.5"])
    assert r.exit_code == 0
    r2 = CliRunner().invoke(org_config_get, ["numorg", "--json"])
    cfg = json.loads(r2.output)
    assert cfg["max_ai_usd_per_org_per_week"] == 7.5


import pytest


@pytest.mark.parametrize("key,value,should_pass", [
    ("tracking_stale_days", "7", True),
    ("tracking_stale_days", "0", False),      # below min 1
    ("tracking_stale_days", "366", False),     # above max 365
    ("discord_pulse_regression_threshold", "0.05", True),
    ("discord_pulse_regression_threshold", "1.5", False),
    ("decay_warning_threshold", "0.5", True),
    ("decay_warning_threshold", "1.0", True),  # boundary inclusive
    ("max_ai_usd_per_org_per_week", "100", True),
    ("max_ai_usd_per_org_per_week", "10001", False),  # above max 10000
    ("stuck_run_threshold_hours", "0.5", True),  # boundary inclusive
    ("stuck_run_threshold_hours", "0.1", False),  # below 0.5
])
def test_org_config_range_validation(tmp_path, monkeypatch, key, value, should_pass):
    """org config set rejects out-of-range numeric values."""
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    CliRunner().invoke(org_create, ["rangeorg", "--name", "Range"])
    r = CliRunner().invoke(org_config_set, ["rangeorg", key, value])
    if should_pass:
        assert r.exit_code == 0, f"Expected pass for {key}={value}: {r.output}"
    else:
        assert r.exit_code != 0, f"Expected fail for {key}={value}: {r.output}"
        assert "out of range" in r.output
