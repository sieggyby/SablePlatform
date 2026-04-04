"""Tests for sable-platform cron CLI commands."""
from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from sable_platform.cli.main import cli
from sable_platform.cron import _MARKER


def _mock_crontab(content: str = ""):
    """Return patches for crontab read/write + cli binary lookup."""
    state = {"content": content}

    def read():
        return state["content"]

    def write(new_content):
        state["content"] = new_content

    return state, read, write


class TestCronAdd:
    def test_add_creates_entry(self):
        state, read, write = _mock_crontab("")
        with patch("sable_platform.cron._read_crontab", side_effect=read), \
             patch("sable_platform.cron._write_crontab", side_effect=write), \
             patch("sable_platform.cron._find_cli_binary", return_value="/usr/bin/sable-platform"):
            result = CliRunner().invoke(cli, [
                "cron", "add", "--org", "tig",
                "--workflow", "weekly_client_loop",
                "--schedule", "weekly-thursday",
            ])
            assert result.exit_code == 0
            assert "Added" in result.output
            assert "weekly-thursday" in result.output
            assert f"{_MARKER}:tig:weekly_client_loop" in state["content"]

    def test_add_rejects_duplicate(self):
        existing = (
            f"0 22 * * 4 /usr/bin/sable-platform workflow run weekly_client_loop --org tig "
            f"{_MARKER}:tig:weekly_client_loop\n"
        )
        state, read, write = _mock_crontab(existing)
        with patch("sable_platform.cron._read_crontab", side_effect=read), \
             patch("sable_platform.cron._write_crontab", side_effect=write), \
             patch("sable_platform.cron._find_cli_binary", return_value="/usr/bin/sable-platform"):
            result = CliRunner().invoke(cli, [
                "cron", "add", "--org", "tig",
                "--workflow", "weekly_client_loop",
                "--schedule", "0 22 * * 4",
            ])
            assert result.exit_code == 1
            assert "already exists" in result.output


class TestCronList:
    def test_list_empty(self):
        with patch("sable_platform.cron._read_crontab", return_value=""):
            result = CliRunner().invoke(cli, ["cron", "list"])
            assert result.exit_code == 0
            assert "No sable-platform cron entries" in result.output

    def test_list_shows_entries(self):
        crontab = (
            f"0 22 * * 4 /usr/bin/sable-platform workflow run weekly_client_loop --org tig "
            f"{_MARKER}:tig:weekly_client_loop\n"
        )
        with patch("sable_platform.cron._read_crontab", return_value=crontab):
            result = CliRunner().invoke(cli, ["cron", "list"])
            assert result.exit_code == 0
            assert "tig:weekly_client_loop" in result.output
            assert "0 22 * * 4" in result.output


class TestCronRemove:
    def test_remove_existing(self):
        existing = (
            f"0 22 * * 4 /usr/bin/sable-platform workflow run weekly_client_loop --org tig "
            f"{_MARKER}:tig:weekly_client_loop\n"
        )
        state, read, write = _mock_crontab(existing)
        with patch("sable_platform.cron._read_crontab", side_effect=read), \
             patch("sable_platform.cron._write_crontab", side_effect=write):
            result = CliRunner().invoke(cli, [
                "cron", "remove", "--org", "tig", "--workflow", "weekly_client_loop"
            ])
            assert result.exit_code == 0
            assert "Removed" in result.output

    def test_remove_nonexistent(self):
        state, read, write = _mock_crontab("")
        with patch("sable_platform.cron._read_crontab", side_effect=read), \
             patch("sable_platform.cron._write_crontab", side_effect=write):
            result = CliRunner().invoke(cli, [
                "cron", "remove", "--org", "tig", "--workflow", "nonexistent"
            ])
            assert result.exit_code == 1
            assert "No cron entry found" in result.output


class TestCronPresets:
    def test_presets_shows_all(self):
        result = CliRunner().invoke(cli, ["cron", "presets"])
        assert result.exit_code == 0
        assert "weekly-thursday" in result.output
        assert "0 22 * * 4" in result.output
