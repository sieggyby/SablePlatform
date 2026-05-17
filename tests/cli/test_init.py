"""Tests for sable-platform init command."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import Mock

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
    assert row[0] == 53


def test_init_idempotent(tmp_path):
    db_path = str(tmp_path / "sable.db")
    r1 = CliRunner().invoke(cli, ["init", "--db-path", db_path])
    r2 = CliRunner().invoke(cli, ["init", "--db-path", db_path])
    assert r1.exit_code == 0
    assert r2.exit_code == 0
    assert "53" in r1.output
    assert "53" in r2.output


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


# ---------------------------------------------------------------------------
# T3-AUTH: CLI operator identity enforcement
# ---------------------------------------------------------------------------

def test_cli_requires_operator_id(monkeypatch):
    """Non-exempt commands must fail with exit_code=1 when SABLE_OPERATOR_ID is not set."""
    monkeypatch.delenv("SABLE_OPERATOR_ID", raising=False)
    result = CliRunner().invoke(cli, ["org", "list"])
    # CliRunner catches SystemExit — exit_code reflects the sys.exit() call
    assert result.exit_code == 1
    assert "SABLE_OPERATOR_ID" in result.output


def test_cli_init_exempt_from_operator_id(tmp_path, monkeypatch):
    """init command must succeed even when SABLE_OPERATOR_ID is not set."""
    db_path = str(tmp_path / "sable_no_op.db")
    monkeypatch.delenv("SABLE_OPERATOR_ID", raising=False)
    result = CliRunner().invoke(cli, ["init", "--db-path", db_path])
    assert result.exit_code == 0
    assert "initialized" in result.output


def test_init_uses_postgres_env_and_hides_password(monkeypatch):
    """Postgres init must run Alembic and avoid echoing credentials."""

    class _Conn:
        def execute(self, query):
            assert "schema_version" in str(query)
            return type("Result", (), {"fetchone": lambda self: (30,)})()

        def close(self):
            return None

    mock_alembic = Mock()
    monkeypatch.delenv("SABLE_DB_PATH", raising=False)
    monkeypatch.setenv("SABLE_DATABASE_URL", "postgresql://user:secret@localhost/sable")
    monkeypatch.setattr("sable_platform.db.migrate_pg._run_alembic_upgrade", mock_alembic)
    monkeypatch.setattr("sable_platform.db.connection.get_db", lambda db_path=None: _Conn())

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0
    assert "initialized" in result.output
    assert "secret" not in result.output
    assert "***" in result.output
    mock_alembic.assert_called_once_with("postgresql://user:secret@localhost/sable")


def test_init_db_path_overrides_postgres_env(tmp_path, monkeypatch):
    """Explicit --db-path must force SQLite even if SABLE_DATABASE_URL is set."""
    db_path = str(tmp_path / "forced_sqlite.db")
    monkeypatch.setenv("SABLE_DATABASE_URL", "postgresql://user:secret@localhost/sable")

    result = CliRunner().invoke(cli, ["init", "--db-path", db_path])

    assert result.exit_code == 0
    assert Path(db_path).exists()
