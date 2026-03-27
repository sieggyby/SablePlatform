"""CLI commands for org management."""
from __future__ import annotations

import json
import sys

import click

from sable_platform.db.connection import get_db
from sable_platform.errors import SableError


@click.group("org")
def org() -> None:
    """Manage orgs in sable.db."""


@org.command("create")
@click.argument("org_id")
@click.option("--name", "-n", required=True, help="Display name for the org")
@click.option("--status", default="active", show_default=True,
              type=click.Choice(["active", "inactive"]), help="Org status")
def org_create(org_id: str, name: str, status: str) -> None:
    """Create a new org in sable.db.

    ORG_ID is the short identifier (e.g. 'tig', 'multisynq').
    """
    conn = get_db()
    try:
        existing = conn.execute("SELECT 1 FROM orgs WHERE org_id=?", (org_id,)).fetchone()
        if existing:
            click.echo(f"Org '{org_id}' already exists.", err=True)
            sys.exit(1)
        conn.execute(
            "INSERT INTO orgs (org_id, display_name, status) VALUES (?, ?, ?)",
            (org_id, name, status),
        )
        conn.commit()
        click.echo(f"Created org '{org_id}' ({name}).")
    except SableError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@org.command("list")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def org_list(as_json: bool) -> None:
    """List all orgs."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT org_id, display_name, status, created_at FROM orgs ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()

    if as_json:
        click.echo(json.dumps([dict(r) for r in rows], default=str))
        return

    if not rows:
        click.echo("No orgs found.")
        return

    click.echo(f"{'ORG_ID':<24}  {'NAME':<30}  STATUS")
    click.echo("-" * 65)
    for r in rows:
        click.echo(f"{r['org_id']:<24}  {(r['display_name'] or ''):<30}  {r['status']}")
