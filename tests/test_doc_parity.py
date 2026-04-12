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


def test_cron_docs_use_correct_preset_syntax():
    """Operator docs must use 'cron add --preset', not the nonexistent 'add-preset'."""
    docs_dir = Path(__file__).parent.parent / "docs"
    cli_ref = (docs_dir / "CLI_REFERENCE.md").read_text()
    cross_repo = (docs_dir / "CROSS_REPO_INTEGRATION.md").read_text()
    alert_system = (docs_dir / "ALERT_SYSTEM.md").read_text()
    assert "add-preset" not in cli_ref, (
        "CLI_REFERENCE.md still uses 'add-preset' — should be 'cron add --preset'"
    )
    assert "add-preset" not in cross_repo, (
        "CROSS_REPO_INTEGRATION.md still uses 'add-preset' — should be 'cron add --preset'"
    )
    assert "cron add --preset" in cli_ref, (
        "CLI_REFERENCE.md must contain the correct 'cron add --preset' syntax"
    )
    assert 'cron add --schedule "0 * * * *" -- sable-platform alerts evaluate' not in alert_system, (
        "ALERT_SYSTEM.md still documents an unsupported cron syntax"
    )
    assert "cron add --preset alert_check" in alert_system, (
        "ALERT_SYSTEM.md must point operators at the supported alert_check preset"
    )


def test_agents_md_mentions_postgres():
    """AGENTS.md must mention Postgres/SABLE_DATABASE_URL so auditors don't miss the Postgres runtime."""
    content = (Path(__file__).parent.parent / "AGENTS.md").read_text()
    assert "SABLE_DATABASE_URL" in content or "Postgres" in content or "postgres" in content, (
        "AGENTS.md must reference Postgres or SABLE_DATABASE_URL — "
        "omitting it biases future audits away from Postgres failure modes"
    )


def test_no_duplicate_alembic_tree():
    """Only sable_platform/alembic/ should exist — no repo-root alembic/ drift trap."""
    root = Path(__file__).parent.parent
    assert not (root / "alembic" / "env.py").exists(), (
        "Duplicate alembic/env.py found at repo root — "
        "only sable_platform/alembic/ is used at runtime"
    )


def test_cron_preset_commands_use_valid_flags():
    """WORKFLOW_PRESETS command templates must not use nonexistent CLI flags."""
    from sable_platform.cron import WORKFLOW_PRESETS

    for name, preset in WORKFLOW_PRESETS.items():
        assert "--all-orgs" not in preset.command_template, (
            f"Preset '{name}' uses --all-orgs which is not a valid flag on 'alerts evaluate'"
        )


def test_docs_do_not_reference_alerts_all_orgs_flag():
    """Operator docs must not mention the nonexistent `alerts evaluate --all-orgs` flag."""
    docs_dir = Path(__file__).parent.parent / "docs"
    lifecycle = (docs_dir / "CLIENT_LIFECYCLE.md").read_text()
    assert "--all-orgs" not in lifecycle, (
        "CLIENT_LIFECYCLE.md still references a nonexistent '--all-orgs' flag"
    )
