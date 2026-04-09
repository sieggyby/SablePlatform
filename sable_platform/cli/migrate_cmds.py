"""CLI commands for SQLite -> Postgres data migration."""
from __future__ import annotations

import sys

import click


@click.group("migrate")
def migrate() -> None:
    """Database migration commands."""


@migrate.command("to-postgres")
@click.option(
    "--target-url",
    envvar="SABLE_DATABASE_URL",
    required=True,
    help="PostgreSQL connection URL (or set SABLE_DATABASE_URL).",
)
@click.option(
    "--source-db",
    default=None,
    envvar="SABLE_DB_PATH",
    help="Path to source sable.db (default: ~/.sable/sable.db).",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Truncate target tables before migration.",
)
@click.option(
    "--skip-backup",
    is_flag=True,
    default=False,
    help="Skip SQLite backup before migration.",
)
def to_postgres(
    target_url: str,
    source_db: str | None,
    force: bool,
    skip_backup: bool,
) -> None:
    """Migrate all data from SQLite sable.db to a PostgreSQL database."""
    # 1. Validate psycopg2 is available
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        click.echo(
            "Error: psycopg2 is not installed.\n"
            "Install it with: pip install 'sable-platform[postgres]'",
            err=True,
        )
        sys.exit(1)

    # 2. Validate target URL
    if not target_url.startswith("postgresql"):
        click.echo(
            f"Error: --target-url must be a PostgreSQL URL, got: {target_url[:30]}...",
            err=True,
        )
        sys.exit(1)

    # 3. Resolve source SQLite path
    from pathlib import Path
    from sable_platform.db.connection import sable_db_path

    source_path = Path(source_db) if source_db else sable_db_path()
    if not source_path.exists():
        click.echo(f"Error: Source database not found: {source_path}", err=True)
        sys.exit(1)

    # 4. Backup before migration
    if not skip_backup:
        from sable_platform.db.backup import backup_database

        backup_dir = source_path.parent / "backups"
        try:
            backup_path = backup_database(
                source_path, backup_dir, label="pre_pg_migration"
            )
            click.echo(f"Backup created: {backup_path}")
        except Exception as exc:
            click.echo(f"Backup failed: {exc}", err=True)
            sys.exit(1)

    # 5. Create engines
    from sable_platform.db.engine import get_engine

    source_url = f"sqlite:///{source_path}"
    source_engine = get_engine(source_url)
    target_engine = get_engine(target_url)

    # 6. Run migration
    from sable_platform.db.migrate_pg import MigrationError, run_migration

    click.echo(f"Migrating {source_path} -> {target_url.split('@')[-1]}...")
    try:
        report = run_migration(source_engine, target_engine, force=force)
    except MigrationError as exc:
        click.echo(f"\nMigration FAILED: {exc}", err=True)
        sys.exit(1)

    # 7. Print report
    click.echo(f"\n{'TABLE':<35}  {'SOURCE':>8}  {'TARGET':>8}  STATUS")
    click.echo("-" * 65)
    for tr in report.tables:
        click.echo(
            f"{tr.table_name:<35}  {tr.source_rows:>8}  {tr.target_rows:>8}  {tr.status}"
        )
    click.echo("-" * 65)
    click.echo(
        f"{'TOTAL':<35}  {report.total_source_rows:>8}  "
        f"{report.total_target_rows:>8}  {report.status}"
    )

    if report.status == "success":
        click.echo("\nMigration complete. Set SABLE_DATABASE_URL and restart services.")
    else:
        click.echo(f"\nMigration FAILED: {report.error}", err=True)
        sys.exit(1)
