"""`sable-platform allowlist …` — manage SableWeb portal access from the DB (no redeploy).

The operator surface for the DB-backed allowlist (migration 075). SableWeb merges these
rows UNDER env/file (additive only). Every mutation is audit-stamped. Requires
`SABLE_OPERATOR_ID` (not in main.py's exemption list). AUTH data — handle with care.
"""
from __future__ import annotations

import json
import os
import sys

import click

from sable_platform.db import allowlist as al
from sable_platform.db.audit import log_audit
from sable_platform.db.connection import get_db


def _operator() -> str:
    return os.environ.get("SABLE_OPERATOR_ID", "unknown")


@click.group("allowlist")
def allowlist() -> None:
    """Manage the DB-backed SableWeb allowlist (mig 075). Merged UNDER env/file by SableWeb."""


@allowlist.command("add")
@click.argument("email")
@click.option("--role", required=True, type=click.Choice(list(al.ROLES)))
@click.option("--operator-id", default=None, help="Required for admin/operator/client_ops")
@click.option("--org", default=None, help="Required for client/client_ops")
@click.option("--assigned-orgs", default=None, help="Comma-separated org scope for an operator")
@click.option("--notes", default=None)
@click.option("--disabled", is_flag=True, help="Create the entry disabled")
def allowlist_add(email, role, operator_id, org, assigned_orgs, notes, disabled) -> None:
    """Add/update an allowlist entry (upsert on the lowercased email)."""
    aos = [o.strip() for o in assigned_orgs.split(",") if o.strip()] if assigned_orgs else None
    conn = get_db()
    try:
        norm = al.upsert_entry(
            conn, email, role, operator_id=operator_id, org=org,
            assigned_orgs=aos, enabled=not disabled, notes=notes,
        )
        log_audit(conn, _operator(), "allowlist_add", detail={"email": norm, "role": role})
        click.echo(f"{'(disabled) ' if disabled else ''}{norm} → {role} added.")
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@allowlist.command("list")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.option("--enabled-only", is_flag=True, default=False)
def allowlist_list(as_json, enabled_only) -> None:
    """List allowlist entries."""
    conn = get_db()
    try:
        rows = al.list_entries(conn, enabled_only=enabled_only)
    finally:
        conn.close()
    if as_json:
        click.echo(json.dumps(rows, default=str))
        return
    if not rows:
        click.echo("No allowlist entries.")
        return
    for r in rows:
        flag = "" if r["enabled"] else " (disabled)"
        scope = r.get("org") or (",".join(r["assigned_orgs"]) if r.get("assigned_orgs") else "")
        click.echo(f"  {r['email']:<36} {r['role']:<11} {scope}{flag}")


@allowlist.command("disable")
@click.argument("email")
def allowlist_disable(email) -> None:
    """Soft-disable an entry (stops NEW logins within the cache TTL; does NOT kill a live session)."""
    conn = get_db()
    try:
        n = al.set_enabled(conn, email, False)
        if n == 0:
            click.echo(f"No allowlist entry for '{email}'.", err=True)
            sys.exit(1)
        log_audit(conn, _operator(), "allowlist_disable", detail={"email": email.strip().lower()})
        click.echo(f"Disabled {email.strip().lower()}.")
    finally:
        conn.close()


@allowlist.command("enable")
@click.argument("email")
def allowlist_enable(email) -> None:
    """Re-enable a soft-disabled entry."""
    conn = get_db()
    try:
        n = al.set_enabled(conn, email, True)
        if n == 0:
            click.echo(f"No allowlist entry for '{email}'.", err=True)
            sys.exit(1)
        log_audit(conn, _operator(), "allowlist_enable", detail={"email": email.strip().lower()})
        click.echo(f"Enabled {email.strip().lower()}.")
    finally:
        conn.close()


@allowlist.command("rm")
@click.argument("email")
def allowlist_rm(email) -> None:
    """Hard-delete an allowlist entry."""
    conn = get_db()
    try:
        n = al.remove_entry(conn, email)
        if n == 0:
            click.echo(f"No allowlist entry for '{email}'.", err=True)
            sys.exit(1)
        log_audit(conn, _operator(), "allowlist_rm", detail={"email": email.strip().lower()})
        click.echo(f"Removed {email.strip().lower()}.")
    finally:
        conn.close()
