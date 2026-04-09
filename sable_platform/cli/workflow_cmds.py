"""CLI commands for workflow management."""
from __future__ import annotations

import json
import logging
import sys

import click

log = logging.getLogger(__name__)

from sable_platform.db.compat import get_dialect, now_offset_param
from sable_platform.db.connection import get_db
from sable_platform.db.workflow_store import (
    cancel_workflow_run,
    get_latest_run,
    get_workflow_events,
    get_workflow_run,
    get_workflow_steps,
    mark_timed_out_runs,
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
@click.option("--ignore-version-check", is_flag=True, default=False,
              help="Resume even if the workflow definition changed since the run was created.")
def workflow_resume(run_id: str, ignore_version_check: bool) -> None:
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
        runner.resume(run_id, conn=conn, ignore_version_check=ignore_version_check)
    except Exception as exc:
        click.echo(f"Resume failed: {exc}", err=True)
        sys.exit(1)
    finally:
        conn.close()

    click.echo("Resume completed.")
    _print_run_status(run_id)


@workflow.command("unlock")
@click.argument("run_id")
def workflow_unlock(run_id: str) -> None:
    """Force-fail a stuck workflow run to unblock new runs.

    Use when a run is stuck in 'pending' or 'running' state (e.g., after a crash).
    """
    from sable_platform.db.workflow_store import unlock_workflow_run

    conn = get_db()
    try:
        updated = unlock_workflow_run(conn, run_id)
        if updated:
            click.echo(f"Run {run_id} force-failed (unlocked).")
        else:
            click.echo(f"Run {run_id} is not in a lockable state (already completed/failed/cancelled).", err=True)
            sys.exit(1)
    finally:
        conn.close()


@workflow.command("cancel")
@click.argument("run_id")
def workflow_cancel(run_id: str) -> None:
    """Mark a non-terminal workflow run as cancelled."""
    conn = get_db()
    try:
        cancel_workflow_run(conn, run_id)
        click.echo(f"Run {run_id} cancelled.")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@workflow.command("status")
@click.argument("run_id")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def workflow_status(run_id: str, as_json: bool) -> None:
    """Show status of a workflow run."""
    _print_run_status(run_id, as_json=as_json)


@workflow.command("list")
@click.option("--org", required=True, help="Org ID")
@click.option("--workflow", "wf_name", default=None, help="Filter by workflow name")
@click.option("--limit", default=10, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def workflow_list(org: str, wf_name: str | None, limit: int, as_json: bool) -> None:
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

    if as_json:
        click.echo(json.dumps([dict(r) for r in rows], default=str))
        return

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


@workflow.command("gc")
@click.option("--hours", default=6, show_default=True, help="Mark runs stuck in 'running' for >N hours as timed_out.")
def workflow_gc(hours: int) -> None:
    """Mark stuck 'running' workflow runs as timed_out."""
    conn = get_db()
    try:
        run_ids = mark_timed_out_runs(conn, hours=hours)
        for rid in run_ids:
            click.echo(rid)
        click.echo(f"Marked {len(run_ids)} run(s) as timed_out.", err=True)
    finally:
        conn.close()


@workflow.command("preflight")
@click.option("--org", "org_id", default=None, help="Check a specific org (default: all active orgs)")
def workflow_preflight(org_id: str | None) -> None:
    """Health gate — exit 0 if ready, exit 1 with diagnostics if not."""
    from sable_platform.db.cost import get_weekly_spend

    conn = get_db()
    try:
        if org_id:
            org_ids = [org_id]
        else:
            rows = conn.execute("SELECT org_id FROM orgs WHERE status='active'").fetchall()
            org_ids = [r["org_id"] for r in rows]

        any_fail = False
        for oid in org_ids:
            failures: list[str] = []

            # 1. Org exists and is active
            org_row = conn.execute(
                "SELECT status FROM orgs WHERE org_id=?", (oid,)
            ).fetchone()
            if not org_row:
                failures.append(f"org_exists — org '{oid}' not found")
            elif org_row["status"] != "active":
                failures.append(f"org_active — org '{oid}' status is '{org_row['status']}'")

            # 2. No stuck runs
            _dialect = get_dialect(conn)
            _cutoff = now_offset_param("offset", _dialect)
            stuck = conn.execute(
                f"""
                SELECT COUNT(*) as cnt FROM workflow_runs
                WHERE org_id=:oid AND status='running'
                  AND started_at < {_cutoff}
                """,
                {"oid": oid, "offset": "-2 hours"},
            ).fetchone()
            if stuck and stuck["cnt"] > 0:
                failures.append(f"stuck_runs — {stuck['cnt']} run(s) stuck > 2 hours")

            # 3. Budget headroom
            spend = get_weekly_spend(conn, oid)
            cap = 5.0
            try:
                cfg_row = conn.execute(
                    "SELECT config_json FROM orgs WHERE org_id=?", (oid,)
                ).fetchone()
                if cfg_row and cfg_row["config_json"]:
                    import json as _json
                    cfg = _json.loads(cfg_row["config_json"])
                    cap = cfg.get("max_ai_usd_per_org_per_week", cap)
            except Exception as e:
                log.warning("Failed to parse config_json for org %s: %s", oid, e)
            if cap > 0 and spend >= cap * 0.90:
                failures.append(f"budget — ${spend:.2f} / ${cap:.2f} ({spend/cap*100:.0f}% used, >= 90%)")

            # 4. No critical alerts
            crit = conn.execute(
                "SELECT COUNT(*) as cnt FROM alerts WHERE org_id=? AND severity='critical' AND status='new'",
                (oid,),
            ).fetchone()
            if crit and crit["cnt"] > 0:
                failures.append(f"critical_alerts — {crit['cnt']} open critical alert(s)")

            if failures:
                any_fail = True
                for f in failures:
                    click.echo(f"FAIL: {oid} — {f}")
            else:
                click.echo(f"OK: {oid} ready")

        # 5. Downstream adapter reachability (suite-level, checked once — not per-org)
        import os
        from pathlib import Path as _Path
        _adapter_vars = [
            ("SABLE_TRACKING_PATH", "tracking"),
            ("SABLE_SLOPPER_PATH", "slopper"),
            ("SABLE_CULT_GRADER_PATH", "cult_grader"),
            ("SABLE_LEAD_IDENTIFIER_PATH", "lead_identifier"),
        ]
        for env_var, label in _adapter_vars:
            val = os.environ.get(env_var, "")
            if val and not _Path(val).exists():
                any_fail = True
                click.echo(f"FAIL: suite — adapter_{label} — {env_var}={val!r} path does not exist")

    finally:
        conn.close()

    if any_fail:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _print_run_status(run_id: str, as_json: bool = False) -> None:
    conn = get_db()
    try:
        run = get_workflow_run(conn, run_id)
        steps = get_workflow_steps(conn, run_id)
    finally:
        conn.close()

    if as_json:
        if not run:
            click.echo(json.dumps({"error": f"run {run_id!r} not found"}))
            return
        click.echo(json.dumps({"run": dict(run), "steps": [dict(s) for s in steps]}, default=str))
        return

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
