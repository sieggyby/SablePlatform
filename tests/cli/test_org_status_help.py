"""Operator help must distinguish platform sweeps from weekly automation."""

from unittest.mock import Mock

from click.testing import CliRunner

from sable_platform.cli import org_cmds


def test_set_status_help_explains_weekly_roster_without_opening_db(monkeypatch):
    get_db = Mock(side_effect=AssertionError("Help must not open a database"))
    monkeypatch.setattr(org_cmds, "get_db", get_db)

    result = CliRunner().invoke(org_cmds.org, ["set-status", "--help"])

    assert result.exit_code == 0, result.output
    get_db.assert_not_called()
    help_text = " ".join(result.output.split()).replace("`", "")
    assert "It does NOT affect the sable-weekly systemd unit." in help_text
    assert "discover_orgs reads roster.yaml from disk and never consults orgs.status" in help_text
    assert "'inactive' removes it from the PLATFORM --all sweep." in help_text
    assert "removes it from every --all sweep" not in help_text
    assert "That gap is why sable-weekly failed on every run" not in help_text
