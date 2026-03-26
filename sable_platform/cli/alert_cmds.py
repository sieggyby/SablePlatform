"""CLI commands for proactive alerting."""
from __future__ import annotations

import sys

import click

from sable_platform.db.connection import get_db
from sable_platform.db.alerts import (
    list_alerts,
    acknowledge_alert,
    upsert_alert_config,
    get_alert_config,
)
from sable_platform.workflows.alert_evaluator import evaluate_alerts


@click.group(name="alerts")
def alerts() -> None:
    """View and manage proactive alerts."""


@alerts.command("list")
@click.option("--org", default=None, help="Filter by org ID")
@click.option("--severity", default=None,
              type=click.Choice(["critical", "warning", "info"]))
@click.option("--status", default="new",
              type=click.Choice(["new", "acknowledged", "resolved"]))
@click.option("--limit", default=20, show_default=True)
def alerts_list(org: str | None, severity: str | None, status: str, limit: int) -> None:
    """List alerts."""
    conn = get_db()
    try:
        rows = list_alerts(conn, org_id=org, severity=severity, status=status, limit=limit)
        if not rows:
            click.echo("No alerts found.")
            return
        click.echo(f"{'ALERT_ID':<12} {'ORG':<14} {'SEV':<10} {'TYPE':<22} {'TITLE':<40} {'CREATED'}")
        click.echo("-" * 120)
        for r in rows:
            click.echo(
                f"{r['alert_id'][:10]:<12} {(r['org_id'] or '-'):<14} "
                f"{r['severity'].upper():<10} {r['alert_type']:<22} "
                f"{(r['title'] or '')[:38]:<40} {r['created_at']}"
            )
    finally:
        conn.close()


@alerts.command("acknowledge")
@click.argument("alert_id")
@click.option("--operator", default="operator")
def alerts_acknowledge(alert_id: str, operator: str) -> None:
    """Acknowledge an alert."""
    conn = get_db()
    try:
        acknowledge_alert(conn, alert_id, operator)
        click.echo(f"Alert {alert_id} acknowledged by {operator}.")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@alerts.command("evaluate")
@click.option("--org", default=None, help="Org ID (omit to sweep all)")
def alerts_evaluate(org: str | None) -> None:
    """Run alert evaluation for an org (or all orgs)."""
    conn = get_db()
    try:
        alert_ids = evaluate_alerts(conn, org_id=org)
        if alert_ids:
            click.echo(f"Created {len(alert_ids)} alert(s):")
            for aid in alert_ids:
                click.echo(f"  {aid}")
        else:
            click.echo("No new alerts.")
    finally:
        conn.close()


@alerts.group("config")
def alerts_config() -> None:
    """Manage per-org alert configuration."""


@alerts_config.command("set")
@click.option("--org", required=True)
@click.option("--min-severity", default="warning",
              type=click.Choice(["critical", "warning", "info"]))
@click.option("--telegram-chat-id", default=None)
@click.option("--discord-webhook", default=None)
@click.option("--disable", is_flag=True, default=False)
def alerts_config_set(org: str, min_severity: str, telegram_chat_id: str | None,
                      discord_webhook: str | None, disable: bool) -> None:
    """Set alert configuration for an org."""
    conn = get_db()
    try:
        config_id = upsert_alert_config(
            conn, org,
            min_severity=min_severity,
            telegram_chat_id=telegram_chat_id,
            discord_webhook_url=discord_webhook,
            enabled=not disable,
        )
        click.echo(f"Alert config saved for {org} (config_id: {config_id})")
    finally:
        conn.close()


@alerts_config.command("show")
@click.option("--org", required=True)
def alerts_config_show(org: str) -> None:
    """Show alert configuration for an org."""
    conn = get_db()
    try:
        cfg = get_alert_config(conn, org)
        if not cfg:
            click.echo(f"No alert config for {org}. Using defaults (min_severity=warning, disabled delivery).")
            return
        click.echo(f"\nAlert Config — {org}")
        click.echo(f"  Enabled:          {bool(cfg['enabled'])}")
        click.echo(f"  Min severity:     {cfg['min_severity']}")
        click.echo(f"  Telegram chat ID: {cfg['telegram_chat_id'] or '-'}")
        click.echo(f"  Discord webhook:  {cfg['discord_webhook_url'] or '-'}")
    finally:
        conn.close()
