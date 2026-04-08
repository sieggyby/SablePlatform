"""Tests for sable_platform.db.backup module."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from sable_platform.db.backup import backup_database, backup_database_pg, get_backup_size, _prune_old_backups


def _create_test_db(path: Path) -> None:
    """Create a minimal sable.db with a test table and row."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE test_data (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO test_data VALUES (1, 'hello')")
    conn.commit()
    conn.close()


class TestBackupDatabase:
    def test_creates_backup_file(self, tmp_path):
        src = tmp_path / "sable.db"
        _create_test_db(src)
        dest_dir = tmp_path / "backups"

        result = backup_database(src, dest_dir)

        assert result.exists()
        assert result.parent == dest_dir
        assert result.name.startswith("sable_")
        assert result.suffix == ".db"

    def test_backup_contains_source_data(self, tmp_path):
        src = tmp_path / "sable.db"
        _create_test_db(src)
        dest_dir = tmp_path / "backups"

        result = backup_database(src, dest_dir)

        conn = sqlite3.connect(str(result))
        row = conn.execute("SELECT val FROM test_data WHERE id = 1").fetchone()
        conn.close()
        assert row[0] == "hello"

    def test_label_appears_in_filename(self, tmp_path):
        src = tmp_path / "sable.db"
        _create_test_db(src)
        dest_dir = tmp_path / "backups"

        result = backup_database(src, dest_dir, label="pre_migration")

        assert "_pre_migration" in result.name

    def test_creates_dest_dir_if_missing(self, tmp_path):
        src = tmp_path / "sable.db"
        _create_test_db(src)
        dest_dir = tmp_path / "deep" / "nested" / "backups"

        result = backup_database(src, dest_dir)

        assert dest_dir.exists()
        assert result.exists()

    def test_raises_on_missing_source(self, tmp_path):
        src = tmp_path / "nonexistent.db"
        dest_dir = tmp_path / "backups"

        with pytest.raises(FileNotFoundError, match="Source database not found"):
            backup_database(src, dest_dir)

    def test_multiple_backups_coexist(self, tmp_path):
        src = tmp_path / "sable.db"
        _create_test_db(src)
        dest_dir = tmp_path / "backups"

        r1 = backup_database(src, dest_dir, label="first", max_backups=0)
        r2 = backup_database(src, dest_dir, label="second", max_backups=0)

        assert r1.exists()
        assert r2.exists()
        assert r1 != r2

    def test_backup_from_wal_mode_db(self, tmp_path):
        src = tmp_path / "sable.db"
        conn = sqlite3.connect(str(src))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO t VALUES (42)")
        conn.commit()
        conn.close()
        dest_dir = tmp_path / "backups"

        result = backup_database(src, dest_dir)

        bk_conn = sqlite3.connect(str(result))
        row = bk_conn.execute("SELECT id FROM t").fetchone()
        bk_conn.close()
        assert row[0] == 42

    def test_label_rejects_path_separators(self, tmp_path):
        src = tmp_path / "sable.db"
        _create_test_db(src)
        dest_dir = tmp_path / "backups"

        with pytest.raises(ValueError, match="alphanumeric"):
            backup_database(src, dest_dir, label="../../etc/evil")

    def test_label_rejects_spaces(self, tmp_path):
        src = tmp_path / "sable.db"
        _create_test_db(src)
        dest_dir = tmp_path / "backups"

        with pytest.raises(ValueError, match="alphanumeric"):
            backup_database(src, dest_dir, label="has spaces")

    def test_label_allows_hyphens_underscores(self, tmp_path):
        src = tmp_path / "sable.db"
        _create_test_db(src)
        dest_dir = tmp_path / "backups"

        result = backup_database(src, dest_dir, label="pre-deploy_v2")

        assert "_pre-deploy_v2" in result.name

    def test_partial_failure_cleans_up_orphan(self, tmp_path):
        src = tmp_path / "sable.db"
        _create_test_db(src)
        dest_dir = tmp_path / "backups"

        # Patch at module level: make the source connection's .backup() raise.
        from unittest.mock import MagicMock

        real_connect = sqlite3.connect
        call_count = 0

        def fake_connect(path, *a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Source connection — return a mock whose backup() raises
                mock_conn = MagicMock()
                mock_conn.backup.side_effect = sqlite3.OperationalError("disk full")
                return mock_conn
            return real_connect(path, *a, **kw)

        with patch("sable_platform.db.backup.sqlite3.connect", side_effect=fake_connect):
            with pytest.raises(sqlite3.OperationalError, match="disk full"):
                backup_database(src, dest_dir)

        # No orphan backup file should remain
        assert len(list(dest_dir.glob("sable_*.db"))) == 0


class TestBackupDatabasePg:
    def test_rejects_invalid_label(self, tmp_path):
        with pytest.raises(ValueError, match="alphanumeric"):
            backup_database_pg("postgresql://localhost/test", tmp_path, label="../../evil")

    def test_raises_when_pg_dump_missing(self, tmp_path):
        with patch("sable_platform.db.backup.shutil.which", return_value=None):
            with pytest.raises(FileNotFoundError, match="pg_dump"):
                backup_database_pg("postgresql://localhost/test", tmp_path)

    def test_raises_on_pg_dump_failure(self, tmp_path):
        mock_result = type("Result", (), {"returncode": 1, "stderr": "connection refused"})()
        with patch("sable_platform.db.backup.shutil.which", return_value="/usr/bin/pg_dump"):
            with patch("sable_platform.db.backup.subprocess.run", return_value=mock_result):
                with pytest.raises(RuntimeError, match="connection refused"):
                    backup_database_pg("postgresql://localhost/test", tmp_path)
        # Verify no orphan file remains
        assert len(list(tmp_path.glob("sable_*.sql"))) == 0

    def test_creates_sql_backup_on_success(self, tmp_path):
        def fake_run(cmd, **kwargs):
            # Simulate pg_dump writing the output file
            for i, arg in enumerate(cmd):
                if arg == "-f" and i + 1 < len(cmd):
                    Path(cmd[i + 1]).write_text("-- pg_dump output")
            return type("Result", (), {"returncode": 0, "stderr": ""})()

        with patch("sable_platform.db.backup.shutil.which", return_value="/usr/bin/pg_dump"):
            with patch("sable_platform.db.backup.subprocess.run", side_effect=fake_run):
                result = backup_database_pg("postgresql://localhost/test", tmp_path, label="test")

        assert result.exists()
        assert result.suffix == ".sql"
        assert "_test" in result.name

    def test_prunes_old_pg_backups(self, tmp_path):
        # Pre-create old backups
        for i in range(5):
            (tmp_path / f"sable_2026010{i}T000000Z.sql").touch()

        def fake_run(cmd, **kwargs):
            for i, arg in enumerate(cmd):
                if arg == "-f" and i + 1 < len(cmd):
                    Path(cmd[i + 1]).write_text("-- pg_dump output")
            return type("Result", (), {"returncode": 0, "stderr": ""})()

        with patch("sable_platform.db.backup.shutil.which", return_value="/usr/bin/pg_dump"):
            with patch("sable_platform.db.backup.subprocess.run", side_effect=fake_run):
                backup_database_pg("postgresql://localhost/test", tmp_path, max_backups=3)

        # 5 old + 1 new = 6, pruned to 3
        assert len(list(tmp_path.glob("sable_*T*Z*.sql"))) == 3


class TestPruneOldBackups:
    def test_prunes_oldest_when_over_limit(self, tmp_path):
        for i in range(5):
            (tmp_path / f"sable_2026010{i}T000000Z.db").touch()

        removed = _prune_old_backups(tmp_path, max_backups=3)

        remaining = sorted(tmp_path.glob("sable_*T*Z*.db"))
        assert len(remaining) == 3
        assert len(removed) == 2
        # Oldest two were removed
        assert "20260100" in removed[0].name
        assert "20260101" in removed[1].name

    def test_no_pruning_when_under_limit(self, tmp_path):
        for i in range(2):
            (tmp_path / f"sable_2026010{i}T000000Z.db").touch()

        removed = _prune_old_backups(tmp_path, max_backups=5)

        assert len(removed) == 0
        assert len(list(tmp_path.glob("sable_*T*Z*.db"))) == 2

    def test_prunes_to_exactly_max(self, tmp_path):
        for i in range(10):
            (tmp_path / f"sable_2026010{i}T000000Z.db").touch()

        _prune_old_backups(tmp_path, max_backups=3)

        assert len(list(tmp_path.glob("sable_*T*Z*.db"))) == 3

    def test_does_not_touch_non_backup_files(self, tmp_path):
        """Non-backup files matching sable_*.db but without timestamp pattern survive."""
        # Create backup files
        for i in range(3):
            (tmp_path / f"sable_2026010{i}T000000Z.db").touch()
        # Create a non-backup sable-prefixed file (no timestamp pattern)
        decoy = tmp_path / "sable_config.db"
        decoy.touch()

        _prune_old_backups(tmp_path, max_backups=1)

        # Decoy must survive — it doesn't match the timestamp glob
        assert decoy.exists()
        # Only 1 backup remains
        assert len(list(tmp_path.glob("sable_*T*Z*.db"))) == 1


class TestGetBackupSize:
    def test_small_file(self, tmp_path):
        f = tmp_path / "small.db"
        f.write_bytes(b"x" * 512)
        assert "512.0 B" == get_backup_size(f)

    def test_kilobyte_file(self, tmp_path):
        f = tmp_path / "medium.db"
        f.write_bytes(b"x" * 2048)
        assert "KB" in get_backup_size(f)
