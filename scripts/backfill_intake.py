#!/usr/bin/env python3
"""Backfill the client-onboarding manifest for EXISTING live clients (mig 073 / Chunk 5).

For each active (non-prospect) org it seeds — idempotently — the `client_intake` header and
the `client_accounts` registry from what's ALREADY scattered on the `orgs` row
(`twitter_handle`, `discord_server_id`, `config_json.discord_guild_id`), and infers a
best-effort `reply_assist` entitlement when the org has an enabled `relay_clients` row. The
point is to make `onboard status <org>` truthful for the current roster on day one — it does
NOT claim completeness (manifest stays 'draft'; the operator reviews gaps via `status`).

Idempotent (every write is an upsert). Run with --dry-run first.

    python scripts/backfill_intake.py --dry-run
    python scripts/backfill_intake.py            # writes
    python scripts/backfill_intake.py --db-path /alt/sable.db
"""
from __future__ import annotations

import argparse
import json
import sys

from sable_platform.db import onboarding as ob
from sable_platform.db.connection import get_db


def _relay_enabled_orgs(conn) -> set[str]:
    """Orgs with an enabled relay_clients row (signals reply_assist). Best-effort — a
    sable.db below mig 057 has no relay_clients table, so degrade to an empty set."""
    try:
        rows = conn.execute("SELECT org_id FROM relay_clients WHERE enabled = 1").fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def backfill(conn, *, dry_run: bool = False) -> list[dict]:
    """Seed intake + accounts (+ inferred entitlements) for each active non-prospect org.
    Returns a per-org summary of what was (or would be) seeded."""
    relay_orgs = _relay_enabled_orgs(conn)
    orgs = conn.execute(
        "SELECT org_id, display_name, twitter_handle, discord_server_id, config_json "
        "FROM orgs WHERE status = 'active' ORDER BY org_id"
    ).fetchall()

    summary: list[dict] = []
    for row in orgs:
        org_id = row["org_id"]
        try:
            cfg = json.loads(row["config_json"]) if row["config_json"] else {}
        except (TypeError, ValueError):
            # one malformed config_json must not halt the whole-roster batch
            print(f"  ! skipping {org_id}: unparseable config_json", file=sys.stderr)
            continue
        if cfg.get("org_type") == "prospect":
            continue  # prospects aren't clients

        twitter = row["twitter_handle"]
        discord = row["discord_server_id"] or cfg.get("discord_guild_id")
        seeded = {"org_id": org_id, "intake": False, "accounts": [], "entitlements": []}

        if ob.get_intake(conn, org_id) is None:
            seeded["intake"] = True
            if not dry_run:
                ob.upsert_intake(conn, org_id)  # header only; manifest_status stays 'draft'
        if twitter:
            seeded["accounts"].append(f"twitter:{twitter}")
            if not dry_run:
                ob.add_account(conn, org_id, "twitter", twitter, "official")
        if discord:
            seeded["accounts"].append(f"discord:{discord}")
            if not dry_run:
                ob.add_account(conn, org_id, "discord", str(discord), "community")
        if org_id in relay_orgs:
            seeded["entitlements"].append("reply_assist")
            if not dry_run:
                ob.set_entitlement(conn, org_id, "reply_assist", status="active")

        if seeded["intake"] or seeded["accounts"] or seeded["entitlements"]:
            summary.append(seeded)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backfill client-onboarding manifests for live clients.")
    ap.add_argument("--dry-run", action="store_true", help="Show what would be seeded without writing")
    ap.add_argument("--db-path", default=None, help="sable.db path (default: SABLE_DB_PATH / ~/.sable)")
    args = ap.parse_args(argv)

    conn = get_db(args.db_path) if args.db_path else get_db()
    try:
        summary = backfill(conn, dry_run=args.dry_run)
    finally:
        conn.close()

    tag = "[dry-run] would seed" if args.dry_run else "seeded"
    if not summary:
        print("Nothing to backfill (all active orgs already have intake/accounts).")
        return 0
    for s in summary:
        bits = []
        if s["intake"]:
            bits.append("intake")
        if s["accounts"]:
            bits.append("accounts=" + ",".join(s["accounts"]))
        if s["entitlements"]:
            bits.append("entitlements=" + ",".join(s["entitlements"]))
        print(f"  {tag} {s['org_id']}: {'; '.join(bits)}")
    print(f"\n{len(summary)} org(s). Next: `sable-platform onboard status <org>` to see remaining gaps.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
