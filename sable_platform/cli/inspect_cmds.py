"""CLI commands for inspecting the platform DB."""
from __future__ import annotations

import datetime
import json

import click

from sable_platform.db.connection import get_db


@click.group("inspect")
def inspect() -> None:
    """Inspect platform DB state."""


@inspect.command("orgs")
def inspect_orgs() -> None:
    """List all orgs."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT org_id, display_name, status, created_at FROM orgs ORDER BY created_at").fetchall()
    finally:
        conn.close()

    if not rows:
        click.echo("No orgs found.")
        return

    click.echo(f"{'ORG_ID':<24}  {'NAME':<30}  STATUS")
    click.echo("-" * 65)
    for r in rows:
        click.echo(f"{r['org_id']:<24}  {(r['display_name'] or ''):<30}  {r['status']}")


@inspect.command("entities")
@click.argument("org_id")
@click.option("--limit", default=50, show_default=True)
def inspect_entities(org_id: str, limit: int) -> None:
    """List entities for an org."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT entity_id, display_name, status, source FROM entities WHERE org_id=? ORDER BY created_at DESC LIMIT ?",
            (org_id, limit),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        click.echo(f"No entities found for org '{org_id}'.")
        return

    click.echo(f"{'ENTITY_ID':<36}  {'NAME':<30}  STATUS")
    click.echo("-" * 75)
    for r in rows:
        click.echo(f"{r['entity_id']:<36}  {(r['display_name'] or ''):<30}  {r['status']}")


@inspect.command("artifacts")
@click.argument("org_id")
@click.option("--limit", default=20, show_default=True)
def inspect_artifacts(org_id: str, limit: int) -> None:
    """List artifacts for an org."""
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT artifact_id, artifact_type, stale, degraded, created_at, path
            FROM artifacts WHERE org_id=?
            ORDER BY created_at DESC LIMIT ?
            """,
            (org_id, limit),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        click.echo(f"No artifacts found for org '{org_id}'.")
        return

    click.echo(f"{'ID':<6}  {'TYPE':<28}  {'STALE'}  {'DEGRADED'}  CREATED")
    click.echo("-" * 80)
    for r in rows:
        click.echo(
            f"{r['artifact_id']:<6}  {r['artifact_type']:<28}  "
            f"{'Y' if r['stale'] else 'N':<5}  {'Y' if r['degraded'] else 'N':<8}  {r['created_at'] or ''}"
        )


@inspect.command("freshness")
@click.argument("org_id")
def inspect_freshness(org_id: str) -> None:
    """Show data freshness indicators for an org."""
    conn = get_db()
    now = datetime.datetime.now(datetime.timezone.utc)

    try:
        # Latest tracking sync
        track_row = conn.execute(
            "SELECT completed_at, status FROM sync_runs WHERE org_id=? AND sync_type='sable_tracking' ORDER BY started_at DESC LIMIT 1",
            (org_id,),
        ).fetchone()

        # Latest cult grader diagnostic
        diag_row = conn.execute(
            "SELECT completed_at, overall_grade FROM diagnostic_runs WHERE org_id=? AND status='completed' ORDER BY completed_at DESC LIMIT 1",
            (org_id,),
        ).fetchone()

        # Latest strategy brief artifact
        brief_row = conn.execute(
            "SELECT created_at, stale FROM artifacts WHERE org_id=? AND artifact_type='twitter_strategy_brief' ORDER BY created_at DESC LIMIT 1",
            (org_id,),
        ).fetchone()
    finally:
        conn.close()

    def age_str(ts: str | None) -> str:
        if not ts:
            return "never"
        try:
            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            days = (now - dt).days
            return f"{days}d ago ({ts[:10]})"
        except ValueError:
            return ts

    click.echo(f"Freshness for org: {org_id}")
    click.echo("-" * 50)
    click.echo(f"Tracking sync:    {age_str(track_row['completed_at'] if track_row else None)}")
    click.echo(f"Diagnostic:       {age_str(diag_row['completed_at'] if diag_row else None)}" +
               (f"  [grade: {diag_row['overall_grade']}]" if diag_row else ""))
    click.echo(f"Strategy brief:   {age_str(brief_row['created_at'] if brief_row else None)}" +
               (f"  [stale: {'Y' if brief_row and brief_row['stale'] else 'N'}]" if brief_row else ""))
