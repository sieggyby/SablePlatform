"""Dashboard CLI command — single view of what needs attention."""
from __future__ import annotations

import json
import logging
import sys

log = logging.getLogger(__name__)

import click

from sable_platform.db.connection import get_db
from sable_platform.db.alerts import list_alerts
from sable_platform.db.cost import get_weekly_spend


@click.command("dashboard")
@click.option("--org", "org_id", default=None, help="Filter to a specific org")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def dashboard(org_id: str | None, as_json: bool) -> None:
    """Show operator dashboard — what needs attention right now."""
    conn = get_db()
    try:
        if org_id:
            org_ids = [org_id]
        else:
            rows = conn.execute("SELECT org_id FROM orgs WHERE status='active'").fetchall()
            org_ids = [r["org_id"] for r in rows]

        if not org_ids:
            click.echo("No active orgs found.")
            return

        org_data = []
        for oid in org_ids:
            # Open alerts by severity
            alerts_rows = list_alerts(conn, org_id=oid, status="new", limit=1000)
            alerts_by_sev = {}
            for a in alerts_rows:
                sev = a["severity"]
                alerts_by_sev[sev] = alerts_by_sev.get(sev, 0) + 1

            # Stale data
            sync_rows = conn.execute(
                """
                SELECT sync_type,
                       MAX(completed_at) as latest,
                       CAST(julianday('now') - julianday(MAX(completed_at)) AS INTEGER) as age_days
                FROM sync_runs WHERE org_id=? AND status='completed'
                GROUP BY sync_type
                """,
                (oid,),
            ).fetchall()
            stale = {r["sync_type"]: r["age_days"] for r in sync_rows if r["age_days"] and r["age_days"] > 7}

            # Stuck runs
            stuck_row = conn.execute(
                """
                SELECT COUNT(*) as cnt FROM workflow_runs
                WHERE org_id=? AND status='running'
                  AND started_at < datetime('now', '-2 hours')
                """,
                (oid,),
            ).fetchone()
            stuck_count = stuck_row["cnt"] if stuck_row else 0

            # Pending actions
            action_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM actions WHERE org_id=? AND status='pending'",
                (oid,),
            ).fetchone()
            pending_actions = action_row["cnt"] if action_row else 0

            # Budget
            spend = get_weekly_spend(conn, oid)
            cap = 5.0
            try:
                cfg_row = conn.execute(
                    "SELECT config_json FROM orgs WHERE org_id=?", (oid,)
                ).fetchone()
                if cfg_row and cfg_row["config_json"]:
                    cfg = json.loads(cfg_row["config_json"])
                    cap = cfg.get("max_ai_usd_per_org_per_week", cap)
            except Exception as e:
                log.warning("Failed to parse config_json for org %s: %s", oid, e)
            pct_used = (spend / cap * 100) if cap > 0 else None

            # Decay risk
            decay_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM entity_decay_scores WHERE org_id=? AND decay_score >= 0.6",
                (oid,),
            ).fetchone()
            at_risk = decay_row["cnt"] if decay_row else 0

            org_data.append({
                "org_id": oid,
                "alerts": alerts_by_sev,
                "stale_syncs": stale,
                "stuck_runs": stuck_count,
                "pending_actions": pending_actions,
                "budget": {"spend": round(spend, 2), "cap": round(cap, 2),
                           "pct_used": round(pct_used, 1) if pct_used is not None else None},
                "at_risk_entities": at_risk,
            })

        # Urgency sort: critical alerts first, then stale, then total alerts
        def urgency_key(d):
            crit = d["alerts"].get("critical", 0)
            stale_count = len(d["stale_syncs"])
            total_alerts = sum(d["alerts"].values())
            return (-crit, -stale_count, -total_alerts)

        org_data.sort(key=urgency_key)

    finally:
        conn.close()

    if as_json:
        click.echo(json.dumps(org_data, indent=2))
        return

    for d in org_data:
        click.echo(click.style(f"\n  {d['org_id']}", bold=True))
        click.echo("  " + "-" * 50)

        # Alerts
        if d["alerts"]:
            parts = []
            for sev in ("critical", "warning", "info"):
                cnt = d["alerts"].get(sev, 0)
                if cnt:
                    if sev == "critical":
                        parts.append(click.style(f"{cnt} critical", fg="red"))
                    elif sev == "warning":
                        parts.append(click.style(f"{cnt} warning", fg="yellow"))
                    else:
                        parts.append(f"{cnt} info")
            click.echo(f"    Alerts:     {', '.join(parts)}")
        else:
            click.echo("    Alerts:     none")

        if d["stale_syncs"]:
            for stype, days in d["stale_syncs"].items():
                click.echo(f"    Stale:      {stype} ({days}d)")
        if d["stuck_runs"]:
            click.echo(click.style(f"    Stuck runs: {d['stuck_runs']}", fg="red"))
        if d["pending_actions"]:
            click.echo(f"    Pending:    {d['pending_actions']} unclaimed actions")

        budget = d["budget"]
        pct_str = f"{budget['pct_used']:.0f}%" if budget["pct_used"] is not None else "N/A"
        click.echo(f"    Budget:     ${budget['spend']:.2f} / ${budget['cap']:.2f} ({pct_str})")

        if d["at_risk_entities"]:
            click.echo(click.style(f"    Decay risk: {d['at_risk_entities']} entities >= 0.6", fg="yellow"))
