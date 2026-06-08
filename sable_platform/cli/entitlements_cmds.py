"""`sable-platform entitlements …` — the enforcement preflight (ONBOARDING_PHASE2_PLAN.md P2).

Run `preflight` BEFORE ever flipping `ENTITLEMENT_ENFORCEMENT=true`: it reports which active
clients are currently USING a service (relay-enabled → reply_assist; checkin_enabled → checkin)
but LACK the matching entitlement row — i.e. the orgs that would be denied the moment the flag
flips. Flipping the flag with gaps present is a client-down event.
"""
from __future__ import annotations

import json

import click

from sable_platform.db import onboarding as ob
from sable_platform.db.connection import get_db
from sable_platform.db.entitlements import enforcement_enabled


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in ("true", "yes", "1", "on")


@click.group("entitlements")
def entitlements() -> None:
    """Entitlement enforcement tooling (the org_entitlements gate, mig 073)."""


@entitlements.command("preflight")
@click.option("--json", "as_json", is_flag=True, default=False)
def entitlements_preflight(as_json: bool) -> None:
    """Report active orgs that would be DENIED a service they're using if enforcement flips on."""
    conn = get_db()
    try:
        relay: set[str] = set()
        try:
            relay = {r[0] for r in conn.execute("SELECT org_id FROM relay_clients WHERE enabled=1").fetchall()}
        except Exception:
            pass  # sable.db below mig 057 — no relay_clients
        # ALL non-prospect orgs — NOT filtered by orgs.status, because the chokepoints are
        # status-agnostic (they key off relay/checkin signals, not orgs.status). Filtering by
        # status='active' here would under-report a gap on a live org onboarded at a different
        # status. The in-use signals below naturally exclude dormant orgs.
        rows = conn.execute("SELECT org_id, config_json FROM orgs").fetchall()
        gaps: list[dict] = []
        checked = 0
        for row in rows:
            org_id = row["org_id"]
            cfg = json.loads(row["config_json"]) if row["config_json"] else {}
            if cfg.get("org_type") == "prospect":
                continue
            checked += 1
            active = {e["service_key"] for e in ob.list_entitlements(conn, org_id, active_only=True)}
            missing = []
            if org_id in relay and "reply_assist" not in active:
                missing.append("reply_assist")
            if _truthy(cfg.get("checkin_enabled")) and "checkin" not in active:
                missing.append("checkin")
            if missing:
                gaps.append({"org_id": org_id, "missing": missing})
    finally:
        conn.close()

    flag = enforcement_enabled()
    if as_json:
        click.echo(json.dumps({"enforcement_enabled": flag, "orgs_checked": checked, "gaps": gaps}))
        return

    click.echo(f"ENTITLEMENT_ENFORCEMENT is currently {'ON ⚠️' if flag else 'off (dormant)'}.")
    click.echo(f"Checked {checked} active client org(s).")
    if not gaps:
        click.echo("✅ No coverage gaps — every in-use service has a matching entitlement. Safe to flip the flag.")
        return
    click.echo(f"❌ {len(gaps)} org(s) would be DENIED a service they're using if the flag flips:")
    for g in gaps:
        click.echo(f"   {g['org_id']}: missing entitlement(s) {', '.join(g['missing'])}")
    click.echo("\nFix: `sable-platform onboard service add <org> <service_key>` for each, then re-run preflight.")
