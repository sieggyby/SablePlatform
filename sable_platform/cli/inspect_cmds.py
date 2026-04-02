"""CLI commands for inspecting the platform DB."""
from __future__ import annotations

import datetime
import json

import click

from sable_platform.db.connection import get_db
from sable_platform.db.discord_pulse import get_discord_pulse_runs
from sable_platform.db.interactions import list_interactions


@click.group("inspect")
def inspect() -> None:
    """Inspect platform DB state."""


@inspect.command("orgs")
def inspect_orgs() -> None:
    """List all orgs."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT org_id, display_name, status, created_at FROM orgs ORDER BY created_at").fetchall()
    finally:
        conn.close()

    if not rows:
        click.echo("No orgs found.")
        return

    click.echo(f"{'ORG_ID':<24}  {'NAME':<30}  STATUS")
    click.echo("-" * 65)
    for r in rows:
        click.echo(f"{r['org_id']:<24}  {(r['display_name'] or ''):<30}  {r['status']}")


@inspect.command("entities")
@click.argument("org_id")
@click.option("--limit", default=50, show_default=True)
def inspect_entities(org_id: str, limit: int) -> None:
    """List entities for an org."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT entity_id, display_name, status, source FROM entities WHERE org_id=? ORDER BY created_at DESC LIMIT ?",
            (org_id, limit),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        click.echo(f"No entities found for org '{org_id}'.")
        return

    click.echo(f"{'ENTITY_ID':<36}  {'NAME':<30}  STATUS")
    click.echo("-" * 75)
    for r in rows:
        click.echo(f"{r['entity_id']:<36}  {(r['display_name'] or ''):<30}  {r['status']}")


@inspect.command("artifacts")
@click.argument("org_id")
@click.option("--limit", default=20, show_default=True)
def inspect_artifacts(org_id: str, limit: int) -> None:
    """List artifacts for an org."""
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT artifact_id, artifact_type, stale, degraded, created_at, path
            FROM artifacts WHERE org_id=?
            ORDER BY created_at DESC LIMIT ?
            """,
            (org_id, limit),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        click.echo(f"No artifacts found for org '{org_id}'.")
        return

    click.echo(f"{'ID':<6}  {'TYPE':<28}  {'STALE'}  {'DEGRADED'}  CREATED")
    click.echo("-" * 80)
    for r in rows:
        click.echo(
            f"{r['artifact_id']:<6}  {r['artifact_type']:<28}  "
            f"{'Y' if r['stale'] else 'N':<5}  {'Y' if r['degraded'] else 'N':<8}  {r['created_at'] or ''}"
        )


@inspect.command("freshness")
@click.argument("org_id")
def inspect_freshness(org_id: str) -> None:
    """Show data freshness indicators for an org."""
    conn = get_db()
    now = datetime.datetime.now(datetime.timezone.utc)

    try:
        # Latest tracking sync
        track_row = conn.execute(
            "SELECT completed_at, status FROM sync_runs WHERE org_id=? AND sync_type='sable_tracking' ORDER BY started_at DESC LIMIT 1",
            (org_id,),
        ).fetchone()

        # Latest cult grader diagnostic
        diag_row = conn.execute(
            "SELECT completed_at, overall_grade FROM diagnostic_runs WHERE org_id=? AND status='completed' ORDER BY completed_at DESC LIMIT 1",
            (org_id,),
        ).fetchone()

        # Latest strategy brief artifact
        brief_row = conn.execute(
            "SELECT created_at, stale FROM artifacts WHERE org_id=? AND artifact_type='twitter_strategy_brief' ORDER BY created_at DESC LIMIT 1",
            (org_id,),
        ).fetchone()
    finally:
        conn.close()

    def age_str(ts: str | None) -> str:
        if not ts:
            return "never"
        try:
            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            days = (now - dt).days
            return f"{days}d ago ({ts[:10]})"
        except ValueError:
            return ts

    click.echo(f"Freshness for org: {org_id}")
    click.echo("-" * 50)
    click.echo(f"Tracking sync:    {age_str(track_row['completed_at'] if track_row else None)}")
    click.echo(f"Diagnostic:       {age_str(diag_row['completed_at'] if diag_row else None)}" +
               (f"  [grade: {diag_row['overall_grade']}]" if diag_row else ""))
    click.echo(f"Strategy brief:   {age_str(brief_row['created_at'] if brief_row else None)}" +
               (f"  [stale: {'Y' if brief_row and brief_row['stale'] else 'N'}]" if brief_row else ""))


@inspect.command("health")
@click.argument("org_id")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def inspect_health(org_id: str, as_json: bool) -> None:
    """Show unified health summary for an org (syncs, alerts, discord pulse, workflows)."""
    conn = get_db()
    now = datetime.datetime.now(datetime.timezone.utc)

    def _age_str(ts: str | None) -> str:
        if not ts:
            return "never"
        try:
            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return f"{(now - dt).days}d ago ({ts[:10]})"
        except ValueError:
            return ts

    try:
        # Check org exists
        org_row = conn.execute("SELECT display_name FROM orgs WHERE org_id=?", (org_id,)).fetchone()
        if not org_row:
            click.echo(f"Org '{org_id}' not found.", err=True)
            return

        # Sync freshness per type
        sync_rows = conn.execute(
            """
            SELECT sync_type, MAX(completed_at) as latest
            FROM sync_runs WHERE org_id=? AND status='completed'
            GROUP BY sync_type
            """,
            (org_id,),
        ).fetchall()
        sync_map = {r["sync_type"]: r["latest"] for r in sync_rows}

        # Open alerts by severity
        alert_counts = conn.execute(
            """
            SELECT severity, COUNT(*) as cnt
            FROM alerts WHERE org_id=? AND status='new'
            GROUP BY severity
            """,
            (org_id,),
        ).fetchall()
        alerts_by_sev = {r["severity"]: r["cnt"] for r in alert_counts}

        # Latest discord pulse
        pulse_rows = get_discord_pulse_runs(conn, org_id, limit=1)
        pulse = pulse_rows[0] if pulse_rows else None

        # Recent workflows
        wf_rows = conn.execute(
            """
            SELECT workflow_name, status, started_at
            FROM workflow_runs WHERE org_id=?
            ORDER BY started_at DESC LIMIT 5
            """,
            (org_id,),
        ).fetchall()

    finally:
        conn.close()

    if as_json:
        import json as _json
        data = {
            "org_id": org_id,
            "syncs": dict(sync_map),
            "open_alerts": dict(alerts_by_sev),
            "discord_pulse": dict(pulse) if pulse else None,
            "recent_workflows": [dict(r) for r in wf_rows],
        }
        click.echo(_json.dumps(data, indent=2))
        return

    click.echo(f"\nHealth — {org_id} ({org_row['display_name'] or ''})")
    click.echo("=" * 55)

    click.echo("\nSync Freshness")
    click.echo("-" * 40)
    if sync_map:
        for stype, ts in sorted(sync_map.items()):
            click.echo(f"  {stype:<28}  {_age_str(ts)}")
    else:
        click.echo("  (no completed syncs)")

    click.echo("\nOpen Alerts")
    click.echo("-" * 40)
    total = sum(alerts_by_sev.values())
    if total:
        for sev in ("critical", "warning", "info"):
            cnt = alerts_by_sev.get(sev, 0)
            if cnt:
                click.echo(f"  {sev.upper():<12}  {cnt}")
    else:
        click.echo("  (none)")

    click.echo("\nDiscord Pulse (latest)")
    click.echo("-" * 40)
    if pulse:
        click.echo(f"  Date:               {pulse['run_date']}")
        click.echo(f"  WoW retention:      {pulse['wow_retention_rate'] if pulse['wow_retention_rate'] is not None else '-'}")
        click.echo(f"  Echo rate:          {pulse['echo_rate'] if pulse['echo_rate'] is not None else '-'}")
        click.echo(f"  Weekly posters:     {pulse['weekly_active_posters'] if pulse['weekly_active_posters'] is not None else '-'}")
        ret_delta = pulse.get("retention_delta")
        click.echo(f"  Retention delta:    {ret_delta if ret_delta is not None else '-'}")
    else:
        click.echo("  (no pulse data)")

    click.echo("\nRecent Workflows")
    click.echo("-" * 40)
    if wf_rows:
        for r in wf_rows:
            click.echo(f"  {r['workflow_name']:<32}  {r['status']:<12}  {(r['started_at'] or '')[:16]}")
    else:
        click.echo("  (none)")


@inspect.command("interactions")
@click.argument("org_id")
@click.option("--type", "interaction_type", default=None, help="Filter by type: reply|mention|co_mention")
@click.option("--min-count", default=1, show_default=True, help="Minimum interaction count")
@click.option("--limit", default=50, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def inspect_interactions(org_id: str, interaction_type: str | None, min_count: int, limit: int, as_json: bool) -> None:
    """List top interaction edges for an org, sorted by count descending."""
    conn = get_db()
    try:
        rows = list_interactions(
            conn, org_id,
            interaction_type=interaction_type,
            min_count=min_count,
            limit=limit,
        )
    finally:
        conn.close()

    if as_json:
        click.echo(json.dumps([dict(r) for r in rows], indent=2))
        return

    if not rows:
        click.echo(f"No interactions found for org '{org_id}'.")
        return

    click.echo(f"{'SOURCE':<24}  {'TARGET':<24}  {'TYPE':<12}  {'COUNT':>5}  LAST_SEEN")
    click.echo("-" * 85)
    for r in rows:
        click.echo(
            f"{r['source_handle']:<24}  {r['target_handle']:<24}  "
            f"{r['interaction_type']:<12}  {r['count']:>5}  {r['last_seen'] or ''}"
        )
