"""sable-platform CLI entry point."""
from __future__ import annotations

import logging

import click

from sable_platform.cli.workflow_cmds import workflow
from sable_platform.cli.inspect_cmds import inspect


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def cli(verbose: bool) -> None:
    """Sable Platform — suite-level workflow and inspection CLI."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(format="%(levelname)s %(name)s: %(message)s", level=level)


cli.add_command(workflow)
cli.add_command(inspect)
