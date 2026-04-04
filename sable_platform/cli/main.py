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
from sable_platform.cli.dashboard_cmds import dashboard
from sable_platform.cli.watchlist_cmds import watchlist
from sable_platform.cli.webhook_cmds import webhooks
from sable_platform.cli.cron_cmds import cron


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


@cli.command("backup")
@click.option("--db-path", default=None, envvar="SABLE_DB_PATH",
              help="Path to sable.db. Defaults to ~/.sable/sable.db")
@click.option("--dest", default=None, type=click.Path(),
              help="Backup directory. Defaults to ~/.sable/backups/")
@click.option("--label", default=None,
              help="Optional label appended to backup filename (alphanumeric, _, -).")
@click.option("--max-backups", default=10, show_default=True,
              type=click.IntRange(min=0),
              help="Max backups to retain (0 = unlimited).")
def backup(db_path: str | None, dest: str | None, label: str | None, max_backups: int) -> None:
    """Create a backup of sable.db using SQLite online backup API."""
    from pathlib import Path
    from sable_platform.db.backup import backup_database, get_backup_size
    from sable_platform.db.connection import sable_db_path

    source = Path(db_path) if db_path else sable_db_path()
    dest_dir = Path(dest) if dest else source.parent / "backups"

    try:
        result = backup_database(source, dest_dir, label=label, max_backups=max_backups)
        size = get_backup_size(result)
        click.echo(f"Backup created: {result} ({size})")
    except (FileNotFoundError, ValueError) as e:
        click.echo(f"Backup failed: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Backup failed: {e}", err=True)
        sys.exit(1)


cli.add_command(workflow)
cli.add_command(inspect)
cli.add_command(actions)
cli.add_command(outcomes)
cli.add_command(journey)
cli.add_command(alerts)
cli.add_command(org)
cli.add_command(dashboard)
cli.add_command(watchlist)
cli.add_command(webhooks)
cli.add_command(cron)
