"""Doc/help parity tests — catch CLI_REFERENCE.md drift from actual code behaviour."""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from sable_platform.cli.main import cli


def test_cli_reference_migration_count_matches_code():
    """CLI_REFERENCE.md must state the correct number of migrations."""
    from sable_platform.db.connection import _MIGRATIONS
    content = (Path(__file__).parent.parent / "docs" / "CLI_REFERENCE.md").read_text()
    expected = f"{len(_MIGRATIONS)} migrations"
    assert expected in content, (
        f"CLI_REFERENCE.md does not mention '{expected}'. "
        f"Update the init section when adding migrations."
    )


def test_schema_command_default_is_files_not_stdout():
    """schema --help must document that the default output is files, not stdout."""
    result = CliRunner().invoke(cli, ["schema", "--help"])
    assert result.exit_code == 0
    assert "--stdout" in result.output, "--stdout flag must appear in schema --help"
    assert "docs/schemas" in result.output, (
        "schema --help must mention docs/schemas as the default output location"
    )
