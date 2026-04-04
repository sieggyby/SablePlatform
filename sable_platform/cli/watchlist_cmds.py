"""Watchlist CLI commands."""
from __future__ import annotations

import json

import click

from sable_platform.db.connection import get_db
from sable_platform.db.watchlist import (
    add_to_watchlist,
    get_watchlist_changes,
    list_watchlist,
    remove_from_watchlist,
    take_all_snapshots,
)


@click.group("watchlist")
def watchlist() -> None:
    """Manage entity watchlists."""


@watchlist.command("add")
@click.argument("org_id")
@click.argument("entity_id")
@click.option("--note", default=None, help="Operator note")
def watchlist_add(org_id: str, entity_id: str, note: str | None) -> None:
    """Add an entity to the watchlist."""
    conn = get_db()
    try:
        added = add_to_watchlist(conn, org_id, entity_id, "cli", note)
        # Audit log
        from sable_platform.db.audit import log_audit
        log_audit(conn, "cli", "watchlist_add", org_id=org_id, entity_id=entity_id,
                  detail={"note": note} if note else None)
    finally:
        conn.close()

    if added:
        click.echo(f"Added {entity_id} to watchlist for {org_id}.")
    else:
        click.echo(f"Entity {entity_id} already on watchlist for {org_id}.")


@watchlist.command("remove")
@click.argument("org_id")
@click.argument("entity_id")
def watchlist_remove(org_id: str, entity_id: str) -> None:
    """Remove an entity from the watchlist."""
    conn = get_db()
    try:
        removed = remove_from_watchlist(conn, org_id, entity_id)
        if removed:
            from sable_platform.db.audit import log_audit
            log_audit(conn, "cli", "watchlist_remove", org_id=org_id, entity_id=entity_id)
    finally:
        conn.close()

    if removed:
        click.echo(f"Removed {entity_id} from watchlist for {org_id}.")
    else:
        click.echo(f"Entity {entity_id} not on watchlist for {org_id}.")


@watchlist.command("list")
@click.argument("org_id")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def watchlist_list(org_id: str, as_json: bool) -> None:
    """List watched entities for an org."""
    conn = get_db()
    try:
        rows = list_watchlist(conn, org_id)
    finally:
        conn.close()

    if as_json:
        click.echo(json.dumps([dict(r) for r in rows], indent=2))
        return

    if not rows:
        click.echo(f"No watched entities for org '{org_id}'.")
        return

    click.echo(f"{'ENTITY_ID':<32}  {'ADDED_BY':<12}  {'NOTE':<30}  ADDED")
    click.echo("-" * 90)
    for r in rows:
        click.echo(
            f"{r['entity_id']:<32}  {r['added_by']:<12}  "
            f"{(r['note'] or ''):<30}  {(r['created_at'] or '')[:16]}"
        )


@watchlist.command("changes")
@click.argument("org_id")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def watchlist_changes(org_id: str, as_json: bool) -> None:
    """Show recent changes for watched entities."""
    conn = get_db()
    try:
        changes = get_watchlist_changes(conn, org_id)
    finally:
        conn.close()

    if as_json:
        click.echo(json.dumps(changes, indent=2))
        return

    if not changes:
        click.echo("No changes detected.")
        return

    for entry in changes:
        click.echo(f"  {entry['entity_id']}:")
        for ch in entry["changes"]:
            click.echo(f"    - {ch}")


@watchlist.command("snapshot")
@click.argument("org_id")
def watchlist_snapshot(org_id: str) -> None:
    """Take fresh snapshots for all watched entities."""
    conn = get_db()
    try:
        count = take_all_snapshots(conn, org_id)
    finally:
        conn.close()
    click.echo(f"Snapshotted {count} entities for {org_id}.")
