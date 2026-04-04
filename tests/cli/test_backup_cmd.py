"""Tests for sable-platform backup CLI command."""
from __future__ import annotations

import sqlite3

from click.testing import CliRunner

from sable_platform.cli.main import cli


def _create_test_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)")
    conn.execute("INSERT OR REPLACE INTO schema_version VALUES (19)")
    conn.execute("CREATE TABLE IF NOT EXISTS orgs (org_id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()


class TestBackupCommand:
    def test_backup_creates_file(self, tmp_path):
        db_path = str(tmp_path / "sable.db")
        _create_test_db(db_path)
        dest = str(tmp_path / "backups")

        result = CliRunner().invoke(cli, [
            "backup", "--db-path", db_path, "--dest", dest
        ])

        assert result.exit_code == 0
        assert "Backup created" in result.output
        backups = list((tmp_path / "backups").glob("sable_*.db"))
        assert len(backups) == 1

    def test_backup_with_label(self, tmp_path):
        db_path = str(tmp_path / "sable.db")
        _create_test_db(db_path)
        dest = str(tmp_path / "backups")

        result = CliRunner().invoke(cli, [
            "backup", "--db-path", db_path, "--dest", dest, "--label", "pre_deploy"
        ])

        assert result.exit_code == 0
        backups = list((tmp_path / "backups").glob("sable_*_pre_deploy.db"))
        assert len(backups) == 1

    def test_backup_shows_size(self, tmp_path):
        db_path = str(tmp_path / "sable.db")
        _create_test_db(db_path)
        dest = str(tmp_path / "backups")

        result = CliRunner().invoke(cli, [
            "backup", "--db-path", db_path, "--dest", dest
        ])

        assert result.exit_code == 0
        # Size should appear in parentheses
        assert "(" in result.output and ")" in result.output

    def test_backup_fails_on_missing_db(self, tmp_path):
        db_path = str(tmp_path / "nonexistent.db")
        dest = str(tmp_path / "backups")

        result = CliRunner().invoke(cli, [
            "backup", "--db-path", db_path, "--dest", dest
        ])

        assert result.exit_code == 1
        assert "not found" in result.output

    def test_backup_respects_env_var(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "env_sable.db")
        _create_test_db(db_path)
        monkeypatch.setenv("SABLE_DB_PATH", db_path)
        dest = str(tmp_path / "backups")

        result = CliRunner().invoke(cli, ["backup", "--dest", dest])

        assert result.exit_code == 0
        assert "Backup created" in result.output

    def test_backup_default_dest_is_backups_subdir(self, tmp_path):
        db_path = str(tmp_path / "sable.db")
        _create_test_db(db_path)

        result = CliRunner().invoke(cli, [
            "backup", "--db-path", db_path
        ])

        assert result.exit_code == 0
        backups_dir = tmp_path / "backups"
        assert backups_dir.exists()
        assert len(list(backups_dir.glob("sable_*.db"))) == 1

    def test_backup_max_backups_pruning(self, tmp_path):
        db_path = str(tmp_path / "sable.db")
        _create_test_db(db_path)
        dest = str(tmp_path / "backups")

        # Create 3 backups with max_backups=2
        for label in ("a", "b", "c"):
            CliRunner().invoke(cli, [
                "backup", "--db-path", db_path, "--dest", dest,
                "--label", label, "--max-backups", "2"
            ])

        backups = list((tmp_path / "backups").glob("sable_*.db"))
        assert len(backups) == 2
