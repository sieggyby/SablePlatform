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
@click.pass_context
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.option("--json-log", is_flag=True, default=False, help="Emit structured JSON logs")
def cli(ctx: click.Context, verbose: bool, json_log: bool) -> None:
    """Sable Platform — suite-level workflow and inspection CLI."""
    import os
    from sable_platform.logging_config import configure_logging
    level = logging.DEBUG if verbose else logging.INFO
    configure_logging(json_mode=json_log, level=level)

    # init is the bootstrap command — exempt from operator identity requirement.
    if ctx.invoked_subcommand not in (None, "init"):
        _op = os.environ.get("SABLE_OPERATOR_ID", "")
        if not _op or _op == "unknown":
            click.echo(
                "Error: SABLE_OPERATOR_ID is not set or is 'unknown'. "
                "Set it to your operator identity before running commands: "
                "export SABLE_OPERATOR_ID=<your_id>",
                err=True,
            )
            sys.exit(1)


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


@cli.command("schema")
@click.option("--output-dir", "-o", default=None, type=click.Path(),
              help="Directory to write JSON Schema files. Defaults to docs/schemas/.")
@click.option("--stdout", "to_stdout", is_flag=True, default=False,
              help="Print schemas to stdout instead of writing files.")
def schema(output_dir: str | None, to_stdout: bool) -> None:
    """Export JSON Schema from canonical Pydantic models."""
    import json as _json
    from pathlib import Path
    from sable_platform.contracts.export import export_schemas

    if to_stdout:
        schemas = export_schemas()
        click.echo(_json.dumps(schemas, indent=2))
    else:
        out = Path(output_dir) if output_dir else Path("docs/schemas")
        schemas = export_schemas(out)
        click.echo(f"Exported {len(schemas)} schemas to {out}/")


@cli.command("health-server")
@click.option("--port", default=8765, show_default=True, help="Port to listen on.")
def health_server(port: int) -> None:
    """Serve GET /health as JSON on the given port (blocks forever)."""
    from sable_platform.http_health import serve_health
    click.echo(f"Health server on :{port} — GET /health")
    serve_health(port)


@cli.command("metrics")
def metrics_cmd() -> None:
    """Print Prometheus-format platform metrics to stdout."""
    from sable_platform.db.connection import get_db
    from sable_platform.metrics import export_metrics
    conn = get_db()
    try:
        click.echo(export_metrics(conn), nl=False)
    finally:
        conn.close()


@cli.command("gc")
@click.option("--retention-days", default=90, show_default=True, type=int,
              help="Delete records older than this many days.")
def gc(retention_days: int) -> None:
    """Purge old workflow events, cost events, and resolved alerts.

    Audit log is NEVER purged.
    """
    from sable_platform.db.connection import get_db
    from sable_platform.db.gc import run_gc

    try:
        conn = get_db()
    except Exception as e:
        click.echo(f"GC failed: {e}", err=True)
        sys.exit(1)
    try:
        counts = run_gc(conn, retention_days=retention_days)
        total = sum(counts.values())
        if total == 0:
            click.echo("Nothing to purge.")
        else:
            for table, count in counts.items():
                if count > 0:
                    click.echo(f"  {table}: {count} rows deleted")
            click.echo(f"Total: {total} rows purged (retention: {retention_days} days).")
    except Exception as e:
        click.echo(f"GC failed: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()
