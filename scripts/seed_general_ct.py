#!/usr/bin/env python3
"""Seed the "General CT" tweet-quality corpus tenant + its account bank.

This is the idempotent, declarative seed that registers a non-client "General
CT Corpus" org and populates the migration-065 quality-account bank
(`relay_quality_accounts`) from the SAME db's `kol_candidates` table, stratified
top-N by KOL strength per follower band. It also pins a small set of
curated/client handles we always want sampled. Safe to re-run: every row is
upserted (or inserted-if-absent) by its natural key, so a second run converges
the DB to the state encoded here rather than duplicating or erroring.

WHY A SCRIPT (not the CLI): there is no CLI yet for `relay_quality_accounts` or
for a corpus-only org. Until one exists, this script IS the seeding ritual. It
also doubles as the Postgres-replay step at deploy time: set
`SABLE_DATABASE_URL=postgresql://...` (or pass `--url`) and re-run.

WHAT IT SEEDS:
  1. orgs                     general_ct / "General CT Corpus" / active / sector=corpus
  2. relay_clients            org_id=general_ct, enabled=1, config.polling.daily_cost_cap_usd=5.0
  3. relay_quality_accounts   ≈80% best-of-CT (cahitarf11) + ≈20% scattered
                              (listid_* lists) + a few fashion, stratified per
                              band by kol_strength_score, PLUS curated/client pins.

SOURCING (≈80% best-of-CT + ≈20% scattered + a few fashion), per follower band:
                       best_of_ct  scattered
  mega  (>= 1e6)            7         15
  large (1e5 .. 1e6)       60         20
  mid   (1e4 .. 1e5)       90         10
  small (1e3 .. 1e4)       70          5
  micro (1 .. 1e3)         25          2
  + 8 genuine fashion-sector accounts (the deliberate few).

best_of_ct = Arf's curated list (cahitarf11) — the richest high-signal *general*
CT source. scattered = the other curated `listid_*` lists (NOT Arf's, NOT
fashion-tagged). The fashion *audience* lists (fabricant/doji/9dcc) + cool_ct are
NOT bulk sources (fashion audiences are full of general CT — fashion people follow
vitalik/toly); genuine fashion is the `fashion` sector tag. All source filters are
portable LIKE-only. Top-N per (band, source) by `kol_strength_score` (NULLs last).
Idempotent: a handle already present is skipped (curated/client pins inserted first
win the collision).

Usage:
    cd ~/Projects/SablePlatform
    python scripts/seed_general_ct.py --dry-run   # show the plan, write nothing
    python scripts/seed_general_ct.py             # seed (local ~/.sable/sable.db by default)
    SABLE_DATABASE_URL=postgresql://... python scripts/seed_general_ct.py   # Postgres replay
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.db.connection import get_sa_engine

# ---------------------------------------------------------------------------
# The desired tenant state (declarative — re-running converges to this).
# ---------------------------------------------------------------------------

ORG_ID = "general_ct"
DISPLAY_NAME = "General CT Corpus"
SECTOR = "corpus"       # NB: a corpus marker, not one of the validated client
                        # sector enums — this org is not a Sable client.
DAILY_COST_CAP_USD = 5.0

# Source-weighted stratification. ~80% "best of CT" (Arf's curated list,
# cahitarf11 — the richest vein of high-signal *general* CT) + ~20% scattered
# high-signal accounts from the other curated `listid_*` lists, + a deliberate
# FEW genuine fashion accounts. The fashion *audience* lists (fabricant/doji/9dcc)
# and cool_ct are NOT bulk sources — fashion audiences are full of general CT
# (fashion people follow vitalik/toly), so genuine fashion = the `fashion` sector
# tag, not list membership.
# band -> (lower_inclusive, upper_exclusive_or_None, n_best_of_ct, n_scattered).
BANDS: list[tuple[str, float, Optional[float], int, int]] = [
    ("mega",  1_000_000.0, None,          7, 15),
    ("large",   100_000.0, 1_000_000.0,  60, 20),
    ("mid",      10_000.0,   100_000.0,  90, 10),
    ("small",     1_000.0,    10_000.0,  70,  5),
    ("micro",         1.0,     1_000.0,  25,  2),
]
FASHION_N = 8  # the deliberate "only a few fashion ppl"

# Source filters on kol_candidates (LIKE-only — portable across SQLite + Postgres,
# no `~` regex). `list:listid` matches the numeric curated lists; the `_` in the
# stored `list:listid_NNNN` is matched by LIKE's single-char wildcard.
BEST_OF_CT_FILTER = "discovery_sources_json LIKE '%cahitarf11%'"
SCATTERED_FILTER = (
    "discovery_sources_json LIKE '%list:listid%' "
    "AND discovery_sources_json NOT LIKE '%cahitarf11%' "
    "AND (sector_tags_json IS NULL OR sector_tags_json NOT LIKE '%fashion%')"
)
FASHION_FILTER = "sector_tags_json LIKE '%fashion%'"

# Handles we always want in the corpus regardless of the KOL bank. Inserted
# FIRST so they win on the natural-key (handle) collision. (handle, source, band).
# band is informational here ('curated'/'client') — these are not KOL-stratified.
CURATED_HANDLES: list[tuple[str, str, str]] = [
    ("neetocracy", "curated", "curated"),
]
CLIENT_HANDLES: list[tuple[str, str, str]] = [
    ("tigfoundation",    "client", "client"),
    ("dr_johnfletcher",  "client", "client"),
    ("tig_intern",       "client", "client"),
    ("0xwoah",           "client", "client"),
    ("0x_asuka",         "client", "client"),
    ("tigintern",        "client", "client"),
    ("robotmoneyagent",  "client", "client"),
    ("qubitcoinx",       "client", "client"),
]


# ---------------------------------------------------------------------------
# Idempotent upsert helpers (dialect-portable: SELECT-by-natural-key, then
# INSERT or UPDATE — no dialect-specific ON CONFLICT).
# ---------------------------------------------------------------------------

class Plan:
    """Collects human-readable actions for the run summary / dry-run output."""

    def __init__(self) -> None:
        self.actions: list[str] = []
        self.band_counts: dict[str, int] = {}

    def add(self, action: str) -> None:
        self.actions.append(action)
        print(f"  • {action}")

    def bump(self, band: str, n: int = 1) -> None:
        self.band_counts[band] = self.band_counts.get(band, 0) + n


def _scalar(conn: Connection, sql: str, params: dict[str, Any]) -> Optional[Any]:
    row = conn.execute(text(sql), params).fetchone()
    return None if row is None else row[0]


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _norm(handle: str) -> str:
    """Normalize a handle to bare lowercase (no leading @, no whitespace)."""
    return handle.strip().lstrip("@").lower()


def upsert_org(conn: Connection, plan: Plan, *, dry_run: bool) -> None:
    config_json = _js({"sector": SECTOR})
    existing = _scalar(conn, "SELECT org_id FROM orgs WHERE org_id = :o", {"o": ORG_ID})
    if existing is None:
        plan.add(f"INSERT orgs '{ORG_ID}' ({DISPLAY_NAME}, status=active, sector={SECTOR})")
        if not dry_run:
            conn.execute(
                text(
                    "INSERT INTO orgs (org_id, display_name, config_json, status) "
                    "VALUES (:o, :dn, :cfg, 'active')"
                ),
                {"o": ORG_ID, "dn": DISPLAY_NAME, "cfg": config_json},
            )
    else:
        plan.add(f"UPDATE orgs '{ORG_ID}' (refresh display_name/sector)")
        if not dry_run:
            conn.execute(
                text(
                    "UPDATE orgs SET display_name = :dn, config_json = :cfg, status = 'active', "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE org_id = :o"
                ),
                {"o": ORG_ID, "dn": DISPLAY_NAME, "cfg": config_json},
            )


def upsert_relay_client(conn: Connection, plan: Plan, *, dry_run: bool) -> None:
    cfg = _js({"polling": {"daily_cost_cap_usd": DAILY_COST_CAP_USD}})
    existing = _scalar(conn, "SELECT org_id FROM relay_clients WHERE org_id = :o", {"o": ORG_ID})
    if existing is None:
        plan.add(f"INSERT relay_clients (enabled=1; daily_cost_cap_usd={DAILY_COST_CAP_USD})")
        if not dry_run:
            conn.execute(
                text("INSERT INTO relay_clients (org_id, enabled, config) VALUES (:o, 1, :cfg)"),
                {"o": ORG_ID, "cfg": cfg},
            )
    else:
        plan.add(f"UPDATE relay_clients (enabled=1; daily_cost_cap_usd={DAILY_COST_CAP_USD})")
        if not dry_run:
            conn.execute(
                text("UPDATE relay_clients SET enabled = 1, config = :cfg WHERE org_id = :o"),
                {"o": ORG_ID, "cfg": cfg},
            )


def _insert_account_if_absent(
    conn: Connection,
    plan: Plan,
    *,
    handle: str,
    band: Optional[str],
    kol_strength: Optional[float],
    archetype_json: str,
    source: str,
    followers_snapshot: Optional[int],
    dry_run: bool,
    quiet: bool = False,
) -> bool:
    """Insert one relay_quality_accounts row if its handle is absent.

    Returns True if a new row was (or would be) inserted, False if it already
    existed and was skipped. ``quiet`` suppresses per-row plan lines (used for
    the bulk KOL pull, which prints a per-band rollup instead).
    """
    h = _norm(handle)
    if not h:
        return False
    present = _scalar(
        conn, "SELECT 1 FROM relay_quality_accounts WHERE handle = :h", {"h": h}
    )
    if present is not None:
        if not quiet:
            plan.add(f"SKIP relay_quality_accounts '{h}' (already present)")
        return False
    if not quiet:
        plan.add(f"INSERT relay_quality_accounts '{h}' (source={source}, band={band})")
    if not dry_run:
        conn.execute(
            text(
                "INSERT INTO relay_quality_accounts "
                "(handle, band, kol_strength, archetype_json, source, followers_snapshot, active) "
                "VALUES (:h, :b, :ks, :aj, :src, :fs, 1)"
            ),
            {
                "h": h, "b": band, "ks": kol_strength, "aj": archetype_json,
                "src": source, "fs": followers_snapshot,
            },
        )
    return True


def seed_curated_and_client(conn: Connection, plan: Plan, *, dry_run: bool) -> int:
    """Pin curated + client handles FIRST so they win the handle collision."""
    inserted = 0
    for handle, source, band in CURATED_HANDLES + CLIENT_HANDLES:
        if _insert_account_if_absent(
            conn, plan,
            handle=handle, band=band, kol_strength=None,
            archetype_json="[]", source=source, followers_snapshot=None,
            dry_run=dry_run,
        ):
            inserted += 1
            plan.bump(band)
    return inserted


def _select_band_candidates(
    conn: Connection, lo: float, hi: Optional[float], top_n: int, source_filter: str
) -> list[Any]:
    """Top-N kol_candidates in a (follower band, source), by kol_strength_score desc.

    Uses a window function (row_number) so this is a single query and works on
    both SQLite (>=3.25) and Postgres. NULL kol_strength_score sorts last; ties
    broken by followers_snapshot desc then handle for determinism. ``source_filter``
    is a trusted, code-defined LIKE predicate (BEST_OF_CT_FILTER / SCATTERED_FILTER).
    """
    if top_n <= 0:
        return []
    upper_clause = "AND followers_snapshot < :hi" if hi is not None else ""
    params: dict[str, Any] = {"lo": lo, "n": top_n}
    if hi is not None:
        params["hi"] = hi
    sql = f"""
        SELECT handle_normalized, kol_strength_score, archetype_tags_json, followers_snapshot
        FROM (
            SELECT
                handle_normalized,
                kol_strength_score,
                archetype_tags_json,
                followers_snapshot,
                ROW_NUMBER() OVER (
                    ORDER BY
                        CASE WHEN kol_strength_score IS NULL THEN 1 ELSE 0 END,
                        kol_strength_score DESC,
                        followers_snapshot DESC,
                        handle_normalized
                ) AS rn
            FROM kol_candidates
            WHERE followers_snapshot IS NOT NULL
              AND handle_normalized <> ''
              AND status NOT IN ('low_signal','unresolved')
              AND followers_snapshot >= :lo
              {upper_clause}
              AND ({source_filter})
        ) ranked
        WHERE rn <= :n
    """
    return conn.execute(text(sql), params).fetchall()


def _select_fashion(conn: Connection, top_n: int) -> list[Any]:
    """Top-N genuine fashion-sector accounts (>=1k followers) by strength — the few."""
    if top_n <= 0:
        return []
    sql = f"""
        SELECT handle_normalized, kol_strength_score, archetype_tags_json, followers_snapshot
        FROM (
            SELECT handle_normalized, kol_strength_score, archetype_tags_json, followers_snapshot,
                ROW_NUMBER() OVER (
                    ORDER BY
                        CASE WHEN kol_strength_score IS NULL THEN 1 ELSE 0 END,
                        kol_strength_score DESC, followers_snapshot DESC, handle_normalized
                ) AS rn
            FROM kol_candidates
            WHERE followers_snapshot IS NOT NULL AND followers_snapshot >= 1000
              AND handle_normalized <> ''
              AND status NOT IN ('low_signal','unresolved')
              AND ({FASHION_FILTER})
        ) ranked
        WHERE rn <= :n
    """
    return conn.execute(text(sql), {"n": top_n}).fetchall()


def _insert_rows(
    conn: Connection, plan: Plan, rows: list[Any], *, band: str, source: str, dry_run: bool
) -> int:
    """Insert a batch of selected rows (handle absent → new). Returns count inserted."""
    inserted = 0
    for row in rows:
        # CompatConnection rows: index access yields values in select order.
        handle = row[0]
        kol_strength = row[1]
        archetype = row[2] if row[2] is not None else "[]"
        followers = row[3]
        if _insert_account_if_absent(
            conn, plan,
            handle=handle, band=band, kol_strength=kol_strength,
            archetype_json=archetype, source=source,
            followers_snapshot=followers, dry_run=dry_run, quiet=True,
        ):
            inserted += 1
    return inserted


def seed_account_bank(conn: Connection, plan: Plan, *, dry_run: bool) -> int:
    """Populate relay_quality_accounts: ~80% best-of-CT + ~20% scattered + a few fashion.

    Best-of-CT is inserted before scattered per band so, on a handle that appears
    in both, the best_of_ct provenance wins (insert-if-absent). Curated/client pins
    were inserted earlier still, so they win over any KOL row.
    """
    total = 0
    for band, lo, hi, n_best, n_scat in BANDS:
        best_rows = _select_band_candidates(conn, lo, hi, n_best, BEST_OF_CT_FILTER)
        n1 = _insert_rows(conn, plan, best_rows, band=band, source="best_of_ct", dry_run=dry_run)
        scat_rows = _select_band_candidates(conn, lo, hi, n_scat, SCATTERED_FILTER)
        n2 = _insert_rows(conn, plan, scat_rows, band=band, source="scattered", dry_run=dry_run)
        plan.bump(band, n1 + n2)
        total += n1 + n2
        plan.add(
            f"band {band}: best_of_ct +{n1} (of {len(best_rows)} matched), "
            f"scattered +{n2} (of {len(scat_rows)} matched)"
        )
    fashion_rows = _select_fashion(conn, FASHION_N)
    nf = _insert_rows(conn, plan, fashion_rows, band="fashion", source="fashion", dry_run=dry_run)
    plan.bump("fashion", nf)
    total += nf
    plan.add(f"fashion: +{nf} (of {len(fashion_rows)} matched) genuine fashion-sector accounts")
    return total


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def seed(conn: Connection, *, dry_run: bool) -> None:
    plan = Plan()
    print("\nSeeding General CT corpus tenant:")
    upsert_org(conn, plan, dry_run=dry_run)
    upsert_relay_client(conn, plan, dry_run=dry_run)

    print("\nPinning curated + client handles (these win on handle collision):")
    seed_curated_and_client(conn, plan, dry_run=dry_run)

    print("\nPopulating relay_quality_accounts (~80% best-of-CT + ~20% scattered + a few fashion):")
    seed_account_bank(conn, plan, dry_run=dry_run)

    _print_summary(plan)

    if dry_run:
        conn.rollback()
        print(f"\nDRY RUN — {len(plan.actions)} actions planned, 0 written.")
        return

    conn.commit()
    print(f"\nCommitted — {len(plan.actions)} actions.")
    _verify(conn)


def _print_summary(plan: Plan) -> None:
    print("\nAccount summary (rows inserted this run, by band):")
    grand = 0
    # Print the canonical bands first, then any extras (curated/client).
    band_order = [b for b, *_ in BANDS] + ["fashion", "curated", "client"]
    seen = set()
    for band in band_order:
        if band in plan.band_counts:
            n = plan.band_counts[band]
            print(f"    {band:<8} {n}")
            grand += n
            seen.add(band)
    for band, n in plan.band_counts.items():
        if band not in seen:
            print(f"    {band:<8} {n}")
            grand += n
    print(f"    {'TOTAL':<8} {grand}")


def _verify(conn: Connection) -> None:
    print("\nVerification:")
    enabled = _scalar(conn, "SELECT enabled FROM relay_clients WHERE org_id = :o", {"o": ORG_ID})
    print(f"  ✓ relay_clients('{ORG_ID}').enabled = {enabled}")
    total = _scalar(conn, "SELECT COUNT(*) FROM relay_quality_accounts", {})
    print(f"  ✓ relay_quality_accounts total rows = {total}")
    by_source = conn.execute(
        text("SELECT source, COUNT(*) FROM relay_quality_accounts GROUP BY source ORDER BY source")
    ).fetchall()
    for row in by_source:
        print(f"      source={row[0]!r:<12} {row[1]}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Seed the General CT tweet-quality corpus tenant + account bank (idempotent)."
    )
    ap.add_argument("--dry-run", action="store_true", help="Show the plan; write nothing.")
    ap.add_argument("--url", default=None, help="DB URL override (else SABLE_DATABASE_URL / SABLE_DB_PATH / ~/.sable/sable.db).")
    args = ap.parse_args()

    url = args.url or os.environ.get("SABLE_DATABASE_URL")
    if not url:
        db_path = os.environ.get("SABLE_DB_PATH") or str(Path.home() / ".sable" / "sable.db")
        url = f"sqlite:///{db_path}"
    print(f"Target DB: {url}")

    engine = get_sa_engine(url)
    with engine.connect() as conn:
        seed(conn, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
