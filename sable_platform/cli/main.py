"""sable-platform CLI entry point."""
from __future__ import annotations

import logging
import sys

import click

from sable_platform.cli.workflow_cmds import workflow
from sable_platform.cli.inspect_cmds import inspect
from sable_platform.cli.action_cmds import actions
from sable_platform.cli.outcome_cmds import outcomes
from sable_platform.cli.journey_cmds import journey
from sable_platform.cli.alert_cmds import alerts
from sable_platform.cli.org_cmds import org


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def cli(verbose: bool) -> None:
    """Sable Platform — suite-level workflow and inspection CLI."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(format="%(levelname)s %(name)s: %(message)s", level=level)


@cli.command("init")
@click.option("--db-path", default=None, envvar="SABLE_DB_PATH",
              help="Path to sable.db. Defaults to ~/.sable/sable.db")
def init(db_path: str | None) -> None:
    """Initialize sable.db and apply all schema migrations."""
    from sable_platform.db.connection import get_db
    try:
        conn = get_db(db_path)
        row = conn.execute("PRAGMA database_list").fetchone()
        resolved_path = row[2] if row else (db_path or "~/.sable/sable.db")
        version_row = conn.execute("SELECT version FROM schema_version").fetchone()
        version = version_row[0] if version_row else 0
        conn.close()
    except Exception as e:
        click.echo(f"Init failed: {e}", err=True)
        sys.exit(1)
    click.echo(f"sable.db initialized at {resolved_path} (schema version {version}).")


cli.add_command(workflow)
cli.add_command(inspect)
cli.add_command(actions)
cli.add_command(outcomes)
cli.add_command(journey)
cli.add_command(alerts)
cli.add_command(org)
