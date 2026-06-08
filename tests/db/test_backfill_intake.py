"""Tests for scripts/backfill_intake.py (Chunk 5) — seed manifests for live clients,
idempotently, against an in-memory sable.db fixture.
"""
from __future__ import annotations

import json

from scripts.backfill_intake import backfill
from sable_platform.db import onboarding as ob


def _seed_org(conn, org_id, *, status="active", twitter=None, discord=None, org_type="client"):
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, twitter_handle, discord_server_id, status, config_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (org_id, org_id.upper(), twitter, discord, status, json.dumps({"org_type": org_type})),
    )
    conn.commit()


def _enable_relay(conn, org_id):
    conn.execute("INSERT INTO relay_clients (org_id, enabled) VALUES (?, 1)", (org_id,))
    conn.commit()


def test_backfill_seeds_intake_accounts_and_inferred_entitlement(in_memory_db):
    _seed_org(in_memory_db, "tigc", twitter="@tig", discord="555")
    _enable_relay(in_memory_db, "tigc")
    summary = backfill(in_memory_db)
    assert [s["org_id"] for s in summary] == ["tigc"]

    assert ob.get_intake(in_memory_db, "tigc") is not None  # manifest header created
    accts = {(a["platform"], a["handle"], a["role"]) for a in ob.list_accounts(in_memory_db, "tigc")}
    assert ("twitter", "@tig", "official") in accts
    assert ("discord", "555", "community") in accts
    ents = ob.list_entitlements(in_memory_db, "tigc")
    assert [(e["service_key"], e["status"]) for e in ents] == [("reply_assist", "active")]


def test_backfill_skips_prospects_and_inactive(in_memory_db):
    _seed_org(in_memory_db, "prosp", status="active", twitter="@p", org_type="prospect")  # active but prospect
    _seed_org(in_memory_db, "draftclient", status="inactive", twitter="@d")  # not live yet
    summary = backfill(in_memory_db)
    assert summary == []  # neither is a live client
    assert ob.get_intake(in_memory_db, "prosp") is None
    assert ob.get_intake(in_memory_db, "draftclient") is None


def test_backfill_is_idempotent(in_memory_db):
    _seed_org(in_memory_db, "acme", twitter="@acme")
    backfill(in_memory_db)
    backfill(in_memory_db)  # re-run
    accts = ob.list_accounts(in_memory_db, "acme")
    assert len(accts) == 1  # natural-key upsert — no duplicate


def test_backfill_dry_run_writes_nothing(in_memory_db):
    _seed_org(in_memory_db, "acme", twitter="@acme", discord="9")
    summary = backfill(in_memory_db, dry_run=True)
    assert summary and summary[0]["org_id"] == "acme"  # reports what it WOULD do
    assert ob.get_intake(in_memory_db, "acme") is None  # but wrote nothing
    assert ob.list_accounts(in_memory_db, "acme") == []


def test_backfill_handles_db_without_relay_table(in_memory_db, monkeypatch):
    # below mig 057 there's no relay_clients — entitlement inference degrades to none, no crash
    _seed_org(in_memory_db, "acme", twitter="@acme")
    import scripts.backfill_intake as bf

    monkeypatch.setattr(bf, "_relay_enabled_orgs", lambda conn: set())
    summary = bf.backfill(in_memory_db)
    assert summary[0]["entitlements"] == []  # no reply_assist inferred
    assert ("twitter", "@acme", "official") in {
        (a["platform"], a["handle"], a["role"]) for a in ob.list_accounts(in_memory_db, "acme")
    }
