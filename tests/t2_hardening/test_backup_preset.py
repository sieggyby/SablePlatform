"""T2-BACKUP: backup cron preset."""
from __future__ import annotations

from subprocess import CompletedProcess
from unittest.mock import patch

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
    assert entry.org == "global"
    assert entry.schedule == "0 3 * * *"
    assert "backup" in entry.command
    mock_write.assert_called_once()
    written = mock_write.call_args[0][0]
    assert "# sable-platform:global:backup" in written


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


def test_add_preset_non_org_scoped_stamps_global_marker():
    """Non-org-scoped presets are stamped with marker org 'global', not the operator org."""
    with patch("sable_platform.cron._read_crontab", return_value=""), \
         patch("sable_platform.cron._write_crontab") as mock_write, \
         patch("sable_platform.cron._find_cli_binary", return_value="/usr/bin/sable-platform"):
        entry = add_preset("alert_check", "tig")

    assert entry.org == "global"
    written = mock_write.call_args[0][0]
    assert "# sable-platform:global:alert_check" in written
    assert "tig" not in written


def test_add_preset_duplicate_command_different_marker_raises():
    """A preset whose command ignores {org} cannot be installed twice under a second org."""
    existing = (
        "0 */4 * * * /usr/bin/sable-platform alerts evaluate "
        "# sable-platform:tig:alert_check\n"
    )
    with patch("sable_platform.cron._read_crontab", return_value=existing), \
         patch("sable_platform.cron._write_crontab") as mock_write, \
         patch("sable_platform.cron._find_cli_binary", return_value="/usr/bin/sable-platform"):
        with pytest.raises(ValueError, match="already exists"):
            add_preset("alert_check", "relay")
    mock_write.assert_not_called()


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


@pytest.mark.parametrize("preset", ["backup", "alert_check", "gc"])
def test_repeated_global_preset_installation_leaves_one_job(preset):
    """Installing a global preset for another org preserves one job."""
    unrelated = "MAILTO=test@example.invalid\n0 * * * * /usr/bin/other-tool\n"
    content = unrelated

    def run(args, **kwargs):
        nonlocal content
        if args == ["crontab", "-l"]:
            return CompletedProcess(args, 0, content, "")
        assert args == ["crontab", "-"]
        content = kwargs["input"]
        return CompletedProcess(args, 0)

    with patch("sable_platform.cron.subprocess.run", side_effect=run) as mock_run, \
         patch("sable_platform.cron._find_cli_binary", return_value="/usr/bin/sable-platform"):
        first = add_preset(preset, "first_org")
        installed = content
        for org in ("second_org", "first_org"):
            with pytest.raises(ValueError, match="already exists"):
                add_preset(preset, org)
            assert content == installed

    assert content.startswith(unrelated)
    assert first.org == "global"
    assert _parse_entries(content) == [first]
    assert sum(call.args[0] == ["crontab", "-"] for call in mock_run.call_args_list) == 1


def test_org_scoped_preset_keeps_separate_org_jobs():
    """Lead discovery retains its org marker and command argument."""
    with patch("sable_platform.cron._read_crontab", return_value="") as read, \
         patch("sable_platform.cron._write_crontab") as write, \
         patch("sable_platform.cron._find_cli_binary", return_value="/usr/bin/sable-platform"):
        first = add_preset("lead_discovery", "first_org")
        read.return_value = write.call_args.args[0]
        second = add_preset("lead_discovery", "second_org")
    assert first.org == "first_org"
    assert second.org == "second_org"
    assert "--org first_org" in first.command
    assert "--org second_org" in second.command
    assert _parse_entries(write.call_args.args[0]) == [first, second]
