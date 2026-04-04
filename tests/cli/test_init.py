"""Tests for sable-platform init command."""
from __future__ import annotations

import sqlite3

from click.testing import CliRunner

from sable_platform.cli.main import cli


def test_init_creates_schema(tmp_path):
    db_path = str(tmp_path / "sable.db")
    result = CliRunner().invoke(cli, ["init", "--db-path", db_path])
    assert result.exit_code == 0
    assert "initialized" in result.output
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    conn.close()
    assert row[0] == 22


def test_init_idempotent(tmp_path):
    db_path = str(tmp_path / "sable.db")
    r1 = CliRunner().invoke(cli, ["init", "--db-path", db_path])
    r2 = CliRunner().invoke(cli, ["init", "--db-path", db_path])
    assert r1.exit_code == 0
    assert r2.exit_code == 0
    assert "22" in r1.output
    assert "22" in r2.output


def test_init_prints_path(tmp_path):
    db_path = str(tmp_path / "sable.db")
    result = CliRunner().invoke(cli, ["init", "--db-path", db_path])
    assert result.exit_code == 0
    assert str(tmp_path) in result.output


def test_init_uses_env_var(tmp_path, monkeypatch):
    db_path = str(tmp_path / "env_sable.db")
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    result = CliRunner().invoke(cli, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / "env_sable.db").exists()
