"""CLI commands for operator action management."""
from __future__ import annotations

import sys

import click

from sable_platform.db.connection import get_db
from sable_platform.db.actions import (
    create_action,
    claim_action,
    complete_action,
    skip_action,
    list_actions,
    action_summary,
)


@click.group(name="actions")
def actions() -> None:
    """Manage operator actions."""


@actions.command("list")
@click.option("--org", required=True, help="Org ID")
@click.option("--status", type=click.Choice(["pending", "claimed", "completed", "skipped"]), default=None)
@click.option("--limit", default=50, show_default=True)
def actions_list(org: str, status: str | None, limit: int) -> None:
    """List actions for an org."""
    conn = get_db()
    try:
        rows = list_actions(conn, org, status=status, limit=limit)
        if not rows:
            click.echo("No actions found.")
            return
        click.echo(f"{'ACTION_ID':<34} {'STATUS':<10} {'TYPE':<14} {'TITLE':<50} {'OPERATOR':<16} {'CREATED'}")
        click.echo("-" * 140)
        for r in rows:
            click.echo(
                f"{r['action_id']:<34} {r['status']:<10} {r['action_type']:<14} "
                f"{(r['title'] or '')[:48]:<50} {(r['operator'] or '-'):<16} {r['created_at']}"
            )
    finally:
        conn.close()


@actions.command("create")
@click.option("--org", required=True, help="Org ID")
@click.option("--title", required=True)
@click.option("--type", "action_type", default="general",
              type=click.Choice(["dm_outreach", "post_content", "reply_thread", "run_ama", "general"]))
@click.option("--entity", "entity_id", default=None)
@click.option("--description", default=None)
@click.option("--source", default="manual",
              type=click.Choice(["playbook", "strategy_brief", "pulse_meta_recommendation", "manual"]))
def actions_create(org: str, title: str, action_type: str, entity_id: str | None,
                   description: str | None, source: str) -> None:
    """Create a new manual action."""
    conn = get_db()
    try:
        action_id = create_action(
            conn, org, title,
            source=source, action_type=action_type,
            entity_id=entity_id, description=description,
        )
        click.echo(f"Created action: {action_id}")
    finally:
        conn.close()


@actions.command("claim")
@click.argument("action_id")
@click.option("--operator", required=True)
def actions_claim(action_id: str, operator: str) -> None:
    """Claim an action as the assigned operator."""
    conn = get_db()
    try:
        claim_action(conn, action_id, operator)
        click.echo(f"Action {action_id} claimed by {operator}.")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@actions.command("complete")
@click.argument("action_id")
@click.option("--notes", default=None)
def actions_complete(action_id: str, notes: str | None) -> None:
    """Mark an action as completed."""
    conn = get_db()
    try:
        complete_action(conn, action_id, outcome_notes=notes)
        click.echo(f"Action {action_id} marked completed.")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@actions.command("skip")
@click.argument("action_id")
@click.option("--notes", default=None)
def actions_skip(action_id: str, notes: str | None) -> None:
    """Mark an action as skipped."""
    conn = get_db()
    try:
        skip_action(conn, action_id, outcome_notes=notes)
        click.echo(f"Action {action_id} marked skipped.")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@actions.command("summary")
@click.option("--org", required=True, help="Org ID")
def actions_summary(org: str) -> None:
    """Show action execution summary for an org."""
    conn = get_db()
    try:
        s = action_summary(conn, org)
        click.echo(f"\nAction Summary — org: {org}")
        click.echo(f"  Pending:    {s['pending']:>4}")
        click.echo(f"  Claimed:    {s['claimed']:>4}")
        click.echo(f"  Completed:  {s['completed']:>4}")
        click.echo(f"  Skipped:    {s['skipped']:>4}")
        click.echo(f"  Total:      {s['total']:>4}")
        click.echo(f"  Execution rate: {s['execution_rate'] * 100:.1f}%")
        if s["avg_days_to_complete"] is not None:
            click.echo(f"  Avg days to complete: {s['avg_days_to_complete']}")
    finally:
        conn.close()
