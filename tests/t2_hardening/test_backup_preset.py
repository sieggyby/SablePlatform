"""T2-BACKUP: backup cron preset."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from sable_platform.cron import (
    WORKFLOW_PRESETS,
    WorkflowPreset,
    add_preset,
    _parse_entries,
)


def test_backup_preset_exists():
    """'backup' is in WORKFLOW_PRESETS."""
    assert "backup" in WORKFLOW_PRESETS
    wp = WORKFLOW_PRESETS["backup"]
    assert isinstance(wp, WorkflowPreset)
    assert wp.workflow == "backup"
    assert wp.schedule == "0 3 * * *"


def test_alert_check_preset_exists():
    """'alert_check' is in WORKFLOW_PRESETS."""
    assert "alert_check" in WORKFLOW_PRESETS
    wp = WORKFLOW_PRESETS["alert_check"]
    assert wp.schedule == "0 */4 * * *"


def test_gc_preset_exists():
    """'gc' is in WORKFLOW_PRESETS."""
    assert "gc" in WORKFLOW_PRESETS
    wp = WORKFLOW_PRESETS["gc"]
    assert wp.schedule == "0 4 * * 0"


def test_add_preset_creates_entry():
    """add_preset('backup', 'test_org') creates a cron entry."""
    with patch("sable_platform.cron._read_crontab", return_value=""), \
         patch("sable_platform.cron._write_crontab") as mock_write, \
         patch("sable_platform.cron._find_cli_binary", return_value="/usr/local/bin/sable-platform"):
        entry = add_preset("backup", "test_org")

    assert entry.workflow == "backup"
    assert entry.org == "test_org"
    assert entry.schedule == "0 3 * * *"
    assert "backup" in entry.command
    mock_write.assert_called_once()
    written = mock_write.call_args[0][0]
    assert "# sable-platform:test_org:backup" in written


def test_add_preset_unknown_raises():
    """Unknown preset name raises ValueError."""
    with pytest.raises(ValueError, match="Unknown preset"):
        add_preset("nonexistent", "test_org")


def test_add_preset_duplicate_raises():
    """Duplicate org:workflow raises ValueError."""
    existing = "0 3 * * * /usr/bin/sable-platform backup # sable-platform:test_org:backup\n"
    with patch("sable_platform.cron._read_crontab", return_value=existing), \
         patch("sable_platform.cron._find_cli_binary", return_value="/usr/bin/sable-platform"):
        with pytest.raises(ValueError, match="already exists"):
            add_preset("backup", "test_org")


def test_add_preset_invalid_org_raises():
    """Shell-injection org name is rejected."""
    with pytest.raises(ValueError, match="must contain only"):
        add_preset("backup", "test; rm -rf /")


def test_presets_command_shows_workflow_presets():
    """CLI presets command lists workflow presets."""
    from click.testing import CliRunner
    from sable_platform.cli.cron_cmds import presets

    runner = CliRunner()
    result = runner.invoke(presets)
    assert result.exit_code == 0
    assert "backup" in result.output
    assert "alert_check" in result.output
    assert "gc" in result.output
    assert "Workflow presets" in result.output
