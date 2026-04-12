"""sable-platform CLI entry point."""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import click
from sqlalchemy.engine import make_url

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


@dataclass(frozen=True)
class CliDatabaseTarget:
    dialect: str
    connection_url: str
    display: str
    sqlite_path: Path | None = None


def _resolve_cli_database_target(explicit_db_path: str | None) -> CliDatabaseTarget:
    from sable_platform.db.connection import sable_db_path

    if explicit_db_path:
        sqlite_path = Path(explicit_db_path).expanduser()
        return CliDatabaseTarget(
            dialect="sqlite",
            connection_url=f"sqlite:///{sqlite_path}",
            display=str(sqlite_path),
            sqlite_path=sqlite_path,
        )

    database_url = os.environ.get("SABLE_DATABASE_URL", "")
    if database_url:
        parsed = make_url(database_url)
        dialect = parsed.get_backend_name()
        if dialect == "sqlite":
            sqlite_path = None
            display = parsed.render_as_string(hide_password=True)
            if parsed.database and parsed.database != ":memory:":
                sqlite_path = Path(parsed.database).expanduser()
                display = str(sqlite_path)
            return CliDatabaseTarget(
                dialect=dialect,
                connection_url=database_url,
                display=display,
                sqlite_path=sqlite_path,
            )
        return CliDatabaseTarget(
            dialect=dialect,
            connection_url=database_url,
            display=parsed.render_as_string(hide_password=True),
        )

    sqlite_path = sable_db_path()
    return CliDatabaseTarget(
        dialect="sqlite",
        connection_url=f"sqlite:///{sqlite_path}",
        display=str(sqlite_path),
        sqlite_path=sqlite_path,
    )


def _sqlite_db_arg(target: CliDatabaseTarget) -> str | None:
    return str(target.sqlite_path) if target.sqlite_path else None


@click.group()
@click.pass_context
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.option("--json-log", is_flag=True, default=False, help="Emit structured JSON logs")
def cli(ctx: click.Context, verbose: bool, json_log: bool) -> None:
    """Sable Platform — suite-level workflow and inspection CLI."""
    from sable_platform.logging_config import configure_logging
    level = logging.DEBUG if verbose else logging.INFO
    configure_logging(json_mode=json_log, level=level)

    # Bootstrap and health commands must work before operator identity exists.
    if ctx.invoked_subcommand not in (None, "init", "db-health"):
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
@click.option("--db-path", default=None,
              help="Path to sable.db. Defaults to ~/.sable/sable.db")
def init(db_path: str | None) -> None:
    """Initialize the configured database and apply schema migrations."""
    from sable_platform.db.connection import get_db

    target = _resolve_cli_database_target(db_path)
    try:
        if target.dialect == "postgresql":
            from sable_platform.db.migrate_pg import _run_alembic_upgrade

            _run_alembic_upgrade(target.connection_url)

        conn = get_db(_sqlite_db_arg(target))
        version_row = conn.execute("SELECT version FROM schema_version").fetchone()
        version = version_row[0] if version_row else 0
        conn.close()
    except Exception as e:
        click.echo(f"Init failed: {e}", err=True)
        sys.exit(1)
    click.echo(f"Database initialized at {target.display} (schema version {version}).")


@cli.command("backup")
@click.option("--db-path", default=None,
              help="Path to sable.db. Defaults to ~/.sable/sable.db")
@click.option("--dest", default=None, type=click.Path(),
              help="Backup directory. Defaults to ~/.sable/backups/")
@click.option("--label", default=None,
              help="Optional label appended to backup filename (alphanumeric, _, -).")
@click.option("--max-backups", default=10, show_default=True,
              type=click.IntRange(min=0),
              help="Max backups to retain (0 = unlimited).")
def backup(db_path: str | None, dest: str | None, label: str | None, max_backups: int) -> None:
    """Create a backup of sable.db (SQLite online backup or pg_dump)."""
    from pathlib import Path
    from sable_platform.db.backup import backup_database, backup_database_pg, get_backup_size

    target = _resolve_cli_database_target(db_path)

    try:
        if target.dialect == "postgresql":
            dest_dir = Path(dest) if dest else Path.home() / ".sable" / "backups"
            result = backup_database_pg(
                target.connection_url,
                dest_dir,
                label=label,
                max_backups=max_backups,
            )
        else:
            if target.sqlite_path is None:
                raise ValueError("SQLite backups require a file-backed database path.")
            source = target.sqlite_path
            dest_dir = Path(dest) if dest else source.parent / "backups"
            result = backup_database(source, dest_dir, label=label, max_backups=max_backups)
        size = get_backup_size(result)
        click.echo(f"Backup created: {result} ({size})")
    except (FileNotFoundError, ValueError) as e:
        click.echo(f"Backup failed: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Backup failed: {e}", err=True)
        sys.exit(1)


@cli.command("db-health")
@click.option("--db-path", default=None,
              help="Path to sable.db. Defaults to ~/.sable/sable.db")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def db_health(db_path: str | None, as_json: bool) -> None:
    """Check database health without requiring an org-scoped inspect command."""
    from sable_platform.db.engine import get_engine
    from sable_platform.db.health import check_db_health

    target = _resolve_cli_database_target(db_path)
    if target.sqlite_path is not None and not target.sqlite_path.exists():
        result: dict[str, object] = {
            "ok": False,
            "migration_version": 0,
            "org_count": 0,
            "latest_diagnostic_run": None,
            "last_alert_eval_age_hours": None,
            "alert_eval_stale": True,
            "error": f"Database not found: {target.sqlite_path}",
        }
    else:
        try:
            with get_engine(target.connection_url).connect() as conn:
                result = dict(check_db_health(conn))
        except Exception as exc:
            result = {
                "ok": False,
                "migration_version": 0,
                "org_count": 0,
                "latest_diagnostic_run": None,
                "last_alert_eval_age_hours": None,
                "alert_eval_stale": True,
                "error": str(exc),
            }

    if as_json:
        click.echo(json.dumps(result, indent=2))
    elif result["ok"]:
        click.echo(
            f"ok schema_version={result['migration_version']} org_count={result['org_count']}"
        )
    else:
        error = result.get("error")
        detail = f": {error}" if error else ""
        click.echo(f"unhealthy{detail}", err=True)

    if not result["ok"]:
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

from sable_platform.cli.migrate_cmds import migrate
cli.add_command(migrate)


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
