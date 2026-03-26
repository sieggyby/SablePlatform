"""sable-platform CLI entry point."""
from __future__ import annotations

import logging

import click

from sable_platform.cli.workflow_cmds import workflow
from sable_platform.cli.inspect_cmds import inspect
from sable_platform.cli.action_cmds import actions
from sable_platform.cli.outcome_cmds import outcomes
from sable_platform.cli.journey_cmds import journey
from sable_platform.cli.alert_cmds import alerts
from sable_platform.cli.org_cmds import org


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def cli(verbose: bool) -> None:
    """Sable Platform — suite-level workflow and inspection CLI."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(format="%(levelname)s %(name)s: %(message)s", level=level)


cli.add_command(workflow)
cli.add_command(inspect)
cli.add_command(actions)
cli.add_command(outcomes)
cli.add_command(journey)
cli.add_command(alerts)
cli.add_command(org)
