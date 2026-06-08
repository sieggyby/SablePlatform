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


@org.command("reject")
@click.argument("prospect_project_id")
@click.option("--reason", "-r", default=None, help="Optional reason for rejection")
def org_reject(prospect_project_id: str, reason: str | None) -> None:
    """Mark a prospect as rejected (not pursuing).

    Stamps rejected_at on all prospect_scores rows matching PROSPECT_PROJECT_ID.
    Rejected prospects are excluded from default prospect listings.
    """
    from sable_platform.db.prospects import reject_prospect
    from sable_platform.db.audit import log_audit

    conn = get_db()
    try:
        count = reject_prospect(conn, prospect_project_id)
        if count == 0:
            click.echo(f"No prospect scores found for '{prospect_project_id}'.", err=True)
            sys.exit(1)
        detail = {"project_id": prospect_project_id, "rows": count}
        if reason:
            detail["reason"] = reason
        log_audit(conn, "unknown", "prospect_rejected", detail=detail)
        click.echo(f"Rejected '{prospect_project_id}' ({count} score rows stamped).")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@org.group("config")
def org_config() -> None:
    """Read and write per-org configuration (sector, stage, thresholds)."""


@org_config.command("set")
@click.argument("org_id")
@click.argument("key")
@click.argument("value")
def org_config_set(org_id: str, key: str, value: str) -> None:
    """Set a config key for an org.

    Merges KEY=VALUE into config_json. Validates sector/stage enums + numeric ranges via
    the SHARED validator (`sable_platform.db.orgs.set_org_config`) — the same one
    `onboard apply` uses, so the two never drift.

    Examples:
      sable-platform org config set tig sector DeFi
      sable-platform org config set tig stage growth
      sable-platform org config set tig max_ai_usd_per_org_per_week 10.0
    """
    from sable_platform.db.orgs import set_org_config

    conn = get_db()
    try:
        parsed_value = set_org_config(conn, org_id, key, value)
        click.echo(f"Set {org_id}.{key} = {parsed_value!r}")
    except ValueError as e:
        # validation failure OR org-not-found (both raised by set_org_config)
        click.echo(f"{e}", err=True)
        sys.exit(1)
    except SableError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@org_config.command("get")
@click.argument("org_id")
@click.argument("key", required=False, default=None)
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def org_config_get(org_id: str, key: str | None, as_json: bool) -> None:
    """Get config for an org (or a specific key).

    Without KEY, prints all config. With KEY, prints just that value.
    """
    conn = get_db()
    try:
        row = conn.execute("SELECT config_json FROM orgs WHERE org_id=?", (org_id,)).fetchone()
        if not row:
            click.echo(f"Org '{org_id}' not found.", err=True)
            sys.exit(1)
        cfg: dict = json.loads(row["config_json"]) if row["config_json"] else {}
        if key:
            if key not in cfg:
                click.echo(f"Key '{key}' not set for org '{org_id}'.")
                return
            if as_json:
                click.echo(json.dumps({key: cfg[key]}))
            else:
                click.echo(f"{key} = {cfg[key]!r}")
        else:
            if as_json:
                click.echo(json.dumps(cfg))
            elif not cfg:
                click.echo(f"No config set for org '{org_id}'.")
            else:
                for k, v in sorted(cfg.items()):
                    click.echo(f"  {k} = {v!r}")
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@org_config.command("list")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def org_config_list(as_json: bool) -> None:
    """Show config_json for all orgs."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT org_id, display_name, config_json FROM orgs ORDER BY org_id"
        ).fetchall()
    finally:
        conn.close()

    if as_json:
        out = []
        for r in rows:
            cfg = json.loads(r["config_json"]) if r["config_json"] else {}
            out.append({"org_id": r["org_id"], "display_name": r["display_name"], "config": cfg})
        click.echo(json.dumps(out))
        return

    if not rows:
        click.echo("No orgs found.")
        return

    for r in rows:
        cfg = json.loads(r["config_json"]) if r["config_json"] else {}
        click.echo(f"\n{r['org_id']}  ({r['display_name'] or 'no name'})")
        if not cfg:
            click.echo("  (no config)")
        else:
            for k, v in sorted(cfg.items()):
                click.echo(f"  {k} = {v!r}")


@org.command("graduate")
@click.argument("prospect_project_id")
def org_graduate(prospect_project_id: str) -> None:
    """Mark a prospect as graduated (converted to client).

    Stamps graduated_at on all prospect_scores rows matching PROSPECT_PROJECT_ID.
    Graduated prospects are excluded from default prospect listings.
    """
    from sable_platform.db.prospects import graduate_prospect
    from sable_platform.db.audit import log_audit

    conn = get_db()
    try:
        count = graduate_prospect(conn, prospect_project_id)
        if count == 0:
            click.echo(f"No prospect scores found for '{prospect_project_id}'.", err=True)
            sys.exit(1)
        log_audit(conn, "unknown", "prospect_graduated",
                  detail={"project_id": prospect_project_id, "rows": count})
        click.echo(f"Graduated '{prospect_project_id}' ({count} score rows stamped).")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()
