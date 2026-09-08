"""Operator-facing migration help."""

import pytest
from click.testing import CliRunner

from sable_platform.cli.migrate_cmds import migrate


@pytest.mark.parametrize(
    "explanation",
    [
        "On failure, the entire migration transaction rolls back",
        "the target is left unchanged",
        "It is empty only if it was empty before the transaction.",
    ],
)
def test_force_help_explains_rollback_and_target_preservation(explanation):
    result = CliRunner().invoke(migrate, ["to-postgres", "--help"])

    assert result.exit_code == 0, result.output
    force_help = " ".join(
        result.output.split("--force", 1)[1].split("--skip-backup", 1)[0].split()
    )
    assert "Truncate target tables before migration." in force_help
    assert explanation in force_help
