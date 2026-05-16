"""sable-platform sync-from-local — replay laptop SQLite writes into target DB.

See ``sable_platform/db/sync_from_local.py`` for table coverage and idempotency
semantics. This CLI is intentionally thin — it resolves source/target,
prints progress, and exits non-zero on any per-table error.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url

from sable_platform.db.sync_from_local import SyncError, sync_org


def _make_engine(url: str):
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ARG001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()
    return engine


def _default_source_db() -> Path:
    """Resolve the default source SQLite path WITHOUT consulting
    ``SABLE_DATABASE_URL``. If that env var is set (typical when the
    operator is targeting prod), we still want source to default to the
    local laptop DB. Honor ``SABLE_DB_PATH`` for tests."""
    env = os.environ.get("SABLE_DB_PATH")
    if env:
        return Path(env)
    return Path.home() / ".sable" / "sable.db"


@click.command("sync-from-local")
@click.option(
    "--org", "org_id", required=True,
    help="Sable org_id to sync (e.g. 'tig').",
)
@click.option(
    "--target-url", "target_url", required=True,
    help="SQLAlchemy URL for the target DB (e.g. postgresql+psycopg://user:pw@host/db).",
)
@click.option(
    "--source-db", "source_db", default=None,
    help="Path to the source SQLite file. Defaults to $SABLE_DB_PATH or "
         "~/.sable/sable.db. Never auto-reads SABLE_DATABASE_URL.",
)
@click.option(
    "--since", default=None,
    help="Only sync rows with created_at/updated_at >= this ISO timestamp. "
         "Cursors still take effect — this is an additional lower bound.",
)
@click.option(
    "--dry-run", is_flag=True, default=False,
    help="Read source rows but write nothing; print per-table counts.",
)
@click.option(
    "--json", "as_json", is_flag=True, default=False, help="Output as JSON.",
)
def sync_from_local(
    org_id: str,
    target_url: str,
    source_db: str | None,
    since: str | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Replay org-scoped writes from a local SQLite into the target DB.

    Intended for operators who run Cult Grader locally and need their
    output mirrored into the production Postgres. Idempotent: per-table
    cursors live in ``platform_meta`` on the target.

    Source defaults to ~/.sable/sable.db. The source path is NEVER
    derived from SABLE_DATABASE_URL — set ``--source-db`` explicitly if
    you need a different SQLite file.
    """
    src_path = Path(source_db).expanduser() if source_db else _default_source_db()
    if not src_path.exists():
        click.echo(f"Source SQLite not found: {src_path}", err=True)
        sys.exit(1)

    # Display target with creds hidden.
    try:
        target_display = make_url(target_url).render_as_string(hide_password=True)
    except Exception:
        target_display = "<opaque target URL>"

    src_engine = _make_engine(f"sqlite:///{src_path}")
    tgt_engine = _make_engine(target_url)

    click.echo(
        f"source: {src_path}\n"
        f"target: {target_display}\n"
        f"org:    {org_id}\n"
        f"since:  {since or '(cursor only)'}\n"
        f"mode:   {'dry-run' if dry_run else 'write'}\n"
    )

    try:
        report = sync_org(
            src_engine, tgt_engine, org_id,
            since=since, dry_run=dry_run,
        )
    except SyncError as exc:
        click.echo(f"Sync aborted: {exc}", err=True)
        sys.exit(1)
    finally:
        src_engine.dispose()
        tgt_engine.dispose()

    if as_json:
        click.echo(json.dumps({
            "org_id": report.org_id,
            "dry_run": report.dry_run,
            "source_schema_version": report.source_schema_version,
            "target_schema_version": report.target_schema_version,
            "tables": [
                {
                    "table": t.table,
                    "rows_read": t.rows_read,
                    "rows_written": t.rows_written,
                    "rows_skipped": t.rows_skipped,
                    "cursor_before": t.cursor_before,
                    "cursor_after": t.cursor_after,
                    "status": t.status,
                    "error": t.error,
                }
                for t in report.tables
            ],
        }, indent=2))
    else:
        click.echo(
            f"{'TABLE':<28} {'READ':>8} {'WRITE':>8} {'SKIP':>8}  STATUS"
        )
        click.echo("-" * 70)
        for t in report.tables:
            click.echo(
                f"{t.table:<28} {t.rows_read:>8} {t.rows_written:>8} "
                f"{t.rows_skipped:>8}  {t.status}"
                + (f"  ({t.error})" if t.error else "")
            )
        click.echo("-" * 70)
        click.echo(
            f"total written: {report.total_written}"
            f"   schema: {report.target_schema_version}"
        )

    any_error = any(t.status == "error" for t in report.tables)
    if any_error:
        sys.exit(2)
