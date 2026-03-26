"""CLI commands for member journey tracking."""
from __future__ import annotations

import sys

import click

from sable_platform.db.connection import get_db
from sable_platform.db.journey import get_entity_journey, entity_funnel, first_seen_list
from sable_platform.errors import SableError


@click.group(name="journey")
def journey() -> None:
    """View member journey timelines and funnel analytics."""


@journey.command("show")
@click.argument("entity_id")
def journey_show(entity_id: str) -> None:
    """Show the full timeline for an entity."""
    conn = get_db()
    try:
        events = get_entity_journey(conn, entity_id)
        if not events:
            click.echo(f"No journey data found for entity {entity_id}.")
            return

        # Print header
        entity_row = conn.execute(
            "SELECT display_name, org_id FROM entities WHERE entity_id=?", (entity_id,)
        ).fetchone()
        if entity_row:
            click.echo(f"\nJourney: {entity_row['display_name']}  (entity_id: {entity_id})")
            click.echo(f"  Org: {entity_row['org_id']}\n")
        else:
            click.echo(f"\nJourney: {entity_id}\n")

        for ev in events:
            ts = (ev.get("timestamp") or "")[:19]
            etype = ev["type"]

            if etype == "first_seen":
                click.echo(f"  {ts}  FIRST SEEN     source={ev.get('source', '?')}, status={ev.get('status', '?')}")
            elif etype == "tag_change":
                ct = ev.get("change_type", "?").upper()
                conf = f"  conf={ev['confidence']:.2f}" if ev.get("confidence") is not None else ""
                exp = f"  expires={ev['expires_at'][:10]}" if ev.get("expires_at") else ""
                click.echo(f"  {ts}  TAG {ct:<10} {ev.get('tag', '?')}{conf}{exp}")
            elif etype == "action":
                click.echo(f"  {ts}  ACTION CREATED {ev.get('action_type', '?')}  \"{ev.get('title', '')[:60]}\"")
            elif etype == "action_claimed":
                click.echo(f"  {ts}  ACTION CLAIMED operator={ev.get('operator', '?')}")
            elif etype == "action_completed":
                notes = f"  notes=\"{ev['notes'][:60]}\"" if ev.get("notes") else ""
                click.echo(f"  {ts}  ACTION DONE   {notes}")
            elif etype == "action_skipped":
                click.echo(f"  {ts}  ACTION SKIPPED")
            elif etype == "outcome":
                click.echo(
                    f"  {ts}  OUTCOME        {ev.get('outcome_type', '?')}  "
                    f"\"{(ev.get('description') or '')[:60]}\""
                )
    except SableError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@journey.command("funnel")
@click.option("--org", required=True, help="Org ID")
def journey_funnel(org: str) -> None:
    """Show aggregate entity funnel for an org."""
    conn = get_db()
    try:
        f = entity_funnel(conn, org)
        total = f["total_entities"] or 0
        click.echo(f"\nEntity Funnel — {org}")
        click.echo(f"  Total entities:          {total:>6}")
        cc = f["cultist_candidate_count"]
        tc = f["top_contributor_count"]
        click.echo(f"  Cultist candidates:      {cc:>6}  ({cc/total*100:.1f}%)" if total else f"  Cultist candidates:      {cc:>6}")
        click.echo(f"  Top contributors:        {tc:>6}  ({tc/total*100:.1f}%)" if total else f"  Top contributors:        {tc:>6}")
        if f["avg_days_to_cultist"] is not None:
            click.echo(f"  Avg days → cultist:      {f['avg_days_to_cultist']:>6.1f}")
        if f["avg_days_to_top_contributor"] is not None:
            click.echo(f"  Avg days → top contrib:  {f['avg_days_to_top_contributor']:>6.1f}")
    except SableError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@journey.command("first-seen")
@click.option("--org", required=True)
@click.option("--source", default=None,
              type=click.Choice(["cult_doctor", "sable_tracking", "pulse_meta", "manual"]))
@click.option("--limit", default=20, show_default=True)
def journey_first_seen(org: str, source: str | None, limit: int) -> None:
    """List entities ordered by first seen date."""
    conn = get_db()
    try:
        rows = first_seen_list(conn, org, source=source, limit=limit)
        if not rows:
            click.echo("No entities found.")
            return
        click.echo(f"{'ENTITY_ID':<34} {'NAME':<30} {'SOURCE':<16} {'STATUS':<12} {'FIRST SEEN'}")
        click.echo("-" * 110)
        for r in rows:
            click.echo(
                f"{r['entity_id']:<34} {(r['display_name'] or '')[:28]:<30} "
                f"{(r['source'] or '-'):<16} {r['status']:<12} {r['created_at']}"
            )
    except SableError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()
