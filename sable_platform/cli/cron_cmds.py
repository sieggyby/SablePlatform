"""CLI commands for managing scheduled cron jobs."""
from __future__ import annotations

import sys

import click


@click.group("cron")
def cron() -> None:
    """Manage scheduled workflow runs via crontab."""


@cron.command("add")
@click.option("--org", required=True, help="Org ID")
@click.option("--workflow", required=True, help="Workflow name")
@click.option("--schedule", required=True,
              help="Cron expression (5 fields) or preset: hourly, daily, weekly-thursday, etc.")
@click.option("--extra-args", default="", help="Additional CLI args after --org")
def add(org: str, workflow: str, schedule: str, extra_args: str) -> None:
    """Add a scheduled workflow run to crontab."""
    from sable_platform.cron import add_entry, SCHEDULE_PRESETS

    try:
        entry = add_entry(org, workflow, schedule, extra_args=extra_args)
        click.echo(f"Added: {entry.to_line()}")
        if schedule in SCHEDULE_PRESETS:
            click.echo(f"  (preset '{schedule}' → {SCHEDULE_PRESETS[schedule]})")
    except (ValueError, FileNotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cron.command("list")
def list_cmd() -> None:
    """List all sable-platform cron entries."""
    from sable_platform.cron import list_entries

    entries = list_entries()
    if not entries:
        click.echo("No sable-platform cron entries found.")
        return
    for entry in entries:
        click.echo(f"  {entry.schedule}  {entry.org}:{entry.workflow}")
        click.echo(f"    → {entry.command}")


@cron.command("remove")
@click.option("--org", required=True, help="Org ID")
@click.option("--workflow", required=True, help="Workflow name")
def remove(org: str, workflow: str) -> None:
    """Remove a scheduled workflow run from crontab."""
    from sable_platform.cron import remove_entry

    if remove_entry(org, workflow):
        click.echo(f"Removed cron entry for {org}:{workflow}")
    else:
        click.echo(f"No cron entry found for {org}:{workflow}", err=True)
        sys.exit(1)


@cron.command("presets")
def presets() -> None:
    """Show available schedule presets."""
    from sable_platform.cron import SCHEDULE_PRESETS

    for name, expr in sorted(SCHEDULE_PRESETS.items()):
        click.echo(f"  {name:25s} {expr}")
