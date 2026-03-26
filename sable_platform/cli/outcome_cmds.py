"""CLI commands for outcome tracking."""
from __future__ import annotations

import sys

import click

from sable_platform.db.connection import get_db
from sable_platform.db.outcomes import create_outcome, list_outcomes, get_diagnostic_deltas


@click.group(name="outcomes")
def outcomes() -> None:
    """Record and view outcome data."""


@outcomes.command("record")
@click.option("--org", required=True, help="Org ID")
@click.option("--type", "outcome_type", required=True,
              type=click.Choice([
                  "client_signed", "client_churned", "entity_converted",
                  "metric_change", "dm_response", "content_performance", "general"
              ]))
@click.option("--action", "action_id", default=None, help="Linked action ID")
@click.option("--entity", "entity_id", default=None, help="Linked entity ID")
@click.option("--notes", default=None)
@click.option("--operator", default=None)
def outcomes_record(org: str, outcome_type: str, action_id: str | None,
                    entity_id: str | None, notes: str | None, operator: str | None) -> None:
    """Record a new outcome."""
    conn = get_db()
    try:
        oid = create_outcome(
            conn, org, outcome_type,
            entity_id=entity_id,
            action_id=action_id,
            description=notes,
            recorded_by=operator,
        )
        click.echo(f"Recorded outcome: {oid}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@outcomes.command("list")
@click.option("--org", required=True)
@click.option("--type", "outcome_type", default=None)
@click.option("--limit", default=20, show_default=True)
def outcomes_list(org: str, outcome_type: str | None, limit: int) -> None:
    """List outcomes for an org."""
    conn = get_db()
    try:
        rows = list_outcomes(conn, org, outcome_type=outcome_type, limit=limit)
        if not rows:
            click.echo("No outcomes found.")
            return
        click.echo(f"{'OUTCOME_ID':<34} {'TYPE':<20} {'DESCRIPTION':<40} {'CREATED'}")
        click.echo("-" * 110)
        for r in rows:
            click.echo(
                f"{r['outcome_id']:<34} {r['outcome_type']:<20} "
                f"{(r['description'] or '-')[:38]:<40} {r['created_at']}"
            )
    finally:
        conn.close()


@outcomes.command("diagnostic-delta")
@click.option("--org", required=True)
@click.option("--run", "run_id_after", default=None, help="Specific run_id_after (defaults to most recent)")
def outcomes_diagnostic_delta(org: str, run_id_after: str | None) -> None:
    """Show diagnostic metric deltas for an org."""
    conn = get_db()
    try:
        if not run_id_after:
            # Find the most recent run with deltas
            row = conn.execute(
                "SELECT DISTINCT run_id_after FROM diagnostic_deltas WHERE org_id=? ORDER BY created_at DESC LIMIT 1",
                (org,),
            ).fetchone()
            if not row:
                click.echo("No diagnostic deltas found for this org.")
                return
            run_id_after = row["run_id_after"]

        deltas = get_diagnostic_deltas(conn, org, run_id_after=run_id_after)
        if not deltas:
            click.echo(f"No deltas found for run {run_id_after}.")
            return

        # Get run dates
        after_row = conn.execute(
            "SELECT created_at FROM diagnostic_runs WHERE run_id=?", (run_id_after,)
        ).fetchone()
        before_run_id = deltas[0]["run_id_before"] if deltas else "?"
        before_row = conn.execute(
            "SELECT created_at FROM diagnostic_runs WHERE run_id=?", (before_run_id,)
        ).fetchone()

        after_date = (after_row["created_at"] or "?")[:10] if after_row else "?"
        before_date = (before_row["created_at"] or "?")[:10] if before_row else "?"

        click.echo(f"\nDiagnostic Delta — {org}")
        click.echo(f"  After run:  {run_id_after[:12]}...  ({after_date})")
        click.echo(f"  Before run: {before_run_id[:12]}...  ({before_date})\n")
        click.echo(f"  {'METRIC':<32} {'BEFORE':>8} {'AFTER':>8} {'DELTA':>8} {'PCT':>8}")
        click.echo("  " + "-" * 68)
        for d in deltas:
            before_s = f"{d['value_before']:.3g}" if d["value_before"] is not None else "-"
            after_s = f"{d['value_after']:.3g}" if d["value_after"] is not None else "-"
            delta_s = ""
            pct_s = ""
            if d["delta"] is not None:
                delta_s = f"{d['delta']:+.3g}"
            if d["pct_change"] is not None:
                pct_s = f"{d['pct_change'] * 100:+.1f}%"
            click.echo(
                f"  {d['metric_name']:<32} {before_s:>8} {after_s:>8} {delta_s:>8} {pct_s:>8}"
            )
    finally:
        conn.close()
