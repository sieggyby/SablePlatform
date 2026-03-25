"""CLI commands for workflow management."""
from __future__ import annotations

import json
import sys

import click

from sable_platform.db.connection import get_db
from sable_platform.db.workflow_store import (
    get_latest_run,
    get_workflow_events,
    get_workflow_run,
    get_workflow_steps,
)


@click.group("workflow")
def workflow() -> None:
    """Workflow run management."""


@workflow.command("run")
@click.argument("workflow_name")
@click.option("--org", required=True, help="Org ID")
@click.option("--config", "-c", multiple=True, metavar="KEY=VALUE", help="Config key=value pairs")
def workflow_run(workflow_name: str, org: str, config: tuple[str, ...]) -> None:
    """Start a new workflow run."""
    # Parse config key=value pairs
    cfg: dict = {}
    for item in config:
        if "=" not in item:
            click.echo(f"Invalid config format '{item}'. Use KEY=VALUE.", err=True)
            sys.exit(1)
        k, _, v = item.partition("=")
        cfg[k.strip()] = v.strip()
    cfg["org_id"] = org

    from sable_platform.workflows import registry
    from sable_platform.workflows.engine import WorkflowRunner

    try:
        defn = registry.get(workflow_name)
    except KeyError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    click.echo(f"Starting workflow '{workflow_name}' for org '{org}'...")
    runner = WorkflowRunner(defn)
    conn = get_db()
    try:
        run_id = runner.run(org, cfg, conn=conn)
    except Exception as exc:
        click.echo(f"Workflow failed: {exc}", err=True)
        sys.exit(1)
    finally:
        conn.close()

    click.echo(f"Run ID: {run_id}")
    _print_run_status(run_id)


@workflow.command("resume")
@click.argument("run_id")
def workflow_resume(run_id: str) -> None:
    """Resume a failed or interrupted workflow run."""
    conn = get_db()
    run_row = get_workflow_run(conn, run_id)
    conn.close()

    if not run_row:
        click.echo(f"Run '{run_id}' not found.", err=True)
        sys.exit(1)

    workflow_name = run_row["workflow_name"]
    from sable_platform.workflows import registry
    from sable_platform.workflows.engine import WorkflowRunner

    try:
        defn = registry.get(workflow_name)
    except KeyError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    click.echo(f"Resuming run {run_id} ({workflow_name})...")
    runner = WorkflowRunner(defn)
    conn = get_db()
    try:
        runner.resume(run_id, conn=conn)
    except Exception as exc:
        click.echo(f"Resume failed: {exc}", err=True)
        sys.exit(1)
    finally:
        conn.close()

    click.echo("Resume completed.")
    _print_run_status(run_id)


@workflow.command("status")
@click.argument("run_id")
def workflow_status(run_id: str) -> None:
    """Show status of a workflow run."""
    _print_run_status(run_id)


@workflow.command("list")
@click.option("--org", required=True, help="Org ID")
@click.option("--workflow", "wf_name", default=None, help="Filter by workflow name")
@click.option("--limit", default=10, show_default=True)
def workflow_list(org: str, wf_name: str | None, limit: int) -> None:
    """List recent workflow runs for an org."""
    conn = get_db()
    try:
        if wf_name:
            rows = conn.execute(
                "SELECT * FROM workflow_runs WHERE org_id=? AND workflow_name=? ORDER BY created_at DESC LIMIT ?",
                (org, wf_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM workflow_runs WHERE org_id=? ORDER BY created_at DESC LIMIT ?",
                (org, limit),
            ).fetchall()
    finally:
        conn.close()

    if not rows:
        click.echo("No runs found.")
        return

    click.echo(f"{'RUN_ID':<36}  {'WORKFLOW':<30}  {'STATUS':<12}  CREATED_AT")
    click.echo("-" * 90)
    for r in rows:
        click.echo(f"{r['run_id']:<36}  {r['workflow_name']:<30}  {r['status']:<12}  {r['created_at'] or ''}")


@workflow.command("events")
@click.argument("run_id")
def workflow_events(run_id: str) -> None:
    """Show event log for a workflow run."""
    conn = get_db()
    try:
        events = get_workflow_events(conn, run_id)
    finally:
        conn.close()

    if not events:
        click.echo("No events found.")
        return

    for e in events:
        payload = json.loads(e["payload_json"] or "{}")
        payload_str = json.dumps(payload) if payload else ""
        click.echo(f"[{e['created_at']}] {e['event_type']:<20} {payload_str}")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _print_run_status(run_id: str) -> None:
    conn = get_db()
    try:
        run = get_workflow_run(conn, run_id)
        steps = get_workflow_steps(conn, run_id)
    finally:
        conn.close()

    if not run:
        click.echo(f"Run '{run_id}' not found.")
        return

    click.echo(f"\nRun:    {run_id}")
    click.echo(f"Status: {run['status']}")
    if run["error"]:
        click.echo(f"Error:  {run['error']}")
    click.echo(f"\n{'STEP':<30}  {'STATUS':<12}  {'RETRIES'}  ERROR")
    click.echo("-" * 70)
    for s in steps:
        err = (s["error"] or "")[:60]
        click.echo(f"{s['step_name']:<30}  {s['status']:<12}  {s['retries']:<7}  {err}")
