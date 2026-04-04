"""Webhook CLI commands."""
from __future__ import annotations

import json
import secrets

import click

from sable_platform.db.connection import get_db
from sable_platform.db.webhooks import (
    create_subscription,
    delete_subscription,
    list_subscriptions,
)


@click.group("webhooks")
def webhooks() -> None:
    """Manage webhook subscriptions."""


@webhooks.command("add")
@click.argument("org_id")
@click.option("--url", required=True, help="Webhook endpoint URL")
@click.option("--events", required=True, help="Comma-separated event types")
@click.option("--secret", default=None, help="HMAC secret (min 16 chars)")
@click.option("--generate-secret", is_flag=True, default=False, help="Auto-generate a secret")
def webhooks_add(org_id: str, url: str, events: str, secret: str | None, generate_secret: bool) -> None:
    """Add a webhook subscription."""
    if generate_secret:
        secret = secrets.token_hex(32)
        click.echo(f"Generated secret (save this, it won't be shown again): {secret}")
    elif not secret:
        click.echo("Error: --secret or --generate-secret is required.", err=True)
        raise SystemExit(1)

    event_types = [e.strip() for e in events.split(",")]

    conn = get_db()
    try:
        sub_id = create_subscription(conn, org_id, url, event_types, secret)
    finally:
        conn.close()

    click.echo(f"Created webhook subscription {sub_id} for {org_id}.")


@webhooks.command("list")
@click.argument("org_id")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def webhooks_list(org_id: str, as_json: bool) -> None:
    """List webhook subscriptions for an org."""
    conn = get_db()
    try:
        rows = list_subscriptions(conn, org_id)
    finally:
        conn.close()

    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return

    if not rows:
        click.echo(f"No webhook subscriptions for org '{org_id}'.")
        return

    click.echo(f"{'ID':>4}  {'URL':<40}  {'EVENTS':<30}  {'ENABLED'}  FAILURES")
    click.echo("-" * 95)
    for r in rows:
        click.echo(
            f"{r['id']:>4}  {r['url']:<40}  {r['event_types']:<30}  "
            f"{'Y' if r['enabled'] else 'N':<7}  {r['consecutive_failures']}"
        )


@webhooks.command("remove")
@click.argument("subscription_id", type=int)
def webhooks_remove(subscription_id: int) -> None:
    """Remove a webhook subscription by ID."""
    conn = get_db()
    try:
        deleted = delete_subscription(conn, subscription_id)
    finally:
        conn.close()

    if deleted:
        click.echo(f"Deleted subscription {subscription_id}.")
    else:
        click.echo(f"Subscription {subscription_id} not found.")


@webhooks.command("test")
@click.argument("org_id")
@click.argument("subscription_id", type=int)
def webhooks_test(org_id: str, subscription_id: int) -> None:
    """Send a test event to a specific webhook subscription."""
    from sable_platform.webhooks.dispatch import dispatch_event

    conn = get_db()
    try:
        count = dispatch_event(conn, "webhook.test", org_id, {"test": True})
    finally:
        conn.close()

    click.echo(f"Dispatched test event. {count} subscription(s) received it.")
