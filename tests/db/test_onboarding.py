"""Tests for the client-onboarding CRUD + the new client-org writer (migration 073).

Covers `orgs.upsert_client_org` (draft/activate, no prospect cap, COALESCE handles) and
the `db/onboarding.py` helpers (intake header, the accounts registry, docs, entitlements),
including FK enforcement and the DB CHECK constraints. In-memory SQLite (FKs ON).
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from sable_platform.db import onboarding as ob
from sable_platform.db.orgs import upsert_client_org, upsert_prospect_org


def _draft(conn, org_id="acme", name="Acme"):
    upsert_client_org(conn, org_id=org_id, display_name=name)  # status defaults to draft
    return org_id


# --- upsert_client_org -------------------------------------------------------
def test_upsert_client_org_creates_draft_without_prospect_cap(in_memory_db):
    upsert_client_org(in_memory_db, org_id="acme", display_name="Acme")
    row = in_memory_db.execute(
        text("SELECT status, config_json FROM orgs WHERE org_id = 'acme'")
    ).fetchone()
    assert row[0] == "inactive"  # draft (created by `onboard init`, activated at `apply`)
    cfg = json.loads(row[1])
    assert cfg["org_type"] == "client"
    assert "max_ai_usd_per_org_per_week" not in cfg  # NOT the $0.50 prospect cap


def test_upsert_client_org_activate_and_coalesce_handles(in_memory_db):
    upsert_client_org(in_memory_db, org_id="acme", display_name="Acme")
    upsert_client_org(
        in_memory_db, org_id="acme", display_name="Acme Inc", status="active",
        twitter_handle="@acme", discord_server_id="123",
    )
    row = in_memory_db.execute(
        text("SELECT status, display_name, twitter_handle, discord_server_id "
             "FROM orgs WHERE org_id = 'acme'")
    ).fetchone()
    assert row[0] == "active" and row[1] == "Acme Inc"
    assert row[2] == "@acme" and row[3] == "123"
    # re-run with status=None + NULL handles must NOT deactivate or wipe (COALESCE)
    upsert_client_org(in_memory_db, org_id="acme", display_name="Acme Inc")
    row2 = in_memory_db.execute(
        text("SELECT status, twitter_handle FROM orgs WHERE org_id = 'acme'")
    ).fetchone()
    assert row2[0] == "active" and row2[1] == "@acme"


def test_upsert_client_org_flips_existing_prospect_to_client(in_memory_db):
    upsert_prospect_org(in_memory_db, org_id="p1", display_name="P1")  # prospect + $0.50 cap
    upsert_client_org(in_memory_db, org_id="p1", display_name="P1", status="active")
    row = in_memory_db.execute(
        text("SELECT status, config_json FROM orgs WHERE org_id = 'p1'")
    ).fetchone()
    cfg = json.loads(row[1])
    assert row[0] == "active"
    assert cfg["org_type"] == "client"  # flipped prospect -> client
    # the $0.50 prospect throttle MUST be cleared on conversion (else it caps a paying client)
    assert "max_ai_usd_per_org_per_week" not in cfg


def test_upsert_client_org_preserves_existing_client_cap(in_memory_db):
    # A re-apply of an ALREADY-client org must NOT wipe an operator-set client cap
    # (only the prospect flip clears the cap).
    upsert_client_org(in_memory_db, org_id="c1", display_name="C1", status="active")
    in_memory_db.execute(
        text("UPDATE orgs SET config_json = :c WHERE org_id = 'c1'"),
        {"c": json.dumps({"org_type": "client", "max_ai_usd_per_org_per_week": 50})},
    )
    in_memory_db.commit()
    upsert_client_org(in_memory_db, org_id="c1", display_name="C1 Corp")  # re-apply
    cfg = json.loads(
        in_memory_db.execute(text("SELECT config_json FROM orgs WHERE org_id = 'c1'")).fetchone()[0]
    )
    assert cfg["max_ai_usd_per_org_per_week"] == 50  # operator-set client cap preserved


# --- FK enforcement ----------------------------------------------------------
def test_intake_requires_existing_org(in_memory_db):
    with pytest.raises(IntegrityError):
        ob.upsert_intake(in_memory_db, "ghost", primary_contact_email="x@y.z")


def test_account_requires_existing_org(in_memory_db):
    with pytest.raises(IntegrityError):
        ob.add_account(in_memory_db, "ghost", "twitter", "@x", "official")


# --- intake header -----------------------------------------------------------
def test_intake_upsert_partial_and_get(in_memory_db):
    _draft(in_memory_db)
    ob.upsert_intake(
        in_memory_db, "acme", primary_contact_email="ceo@acme.io", website_url="https://acme.io"
    )
    got = ob.get_intake(in_memory_db, "acme")
    assert got["primary_contact_email"] == "ceo@acme.io"
    assert got["manifest_status"] == "draft"  # default on insert
    # a partial update preserves untouched fields
    ob.upsert_intake(in_memory_db, "acme", notes="hot lead")
    got2 = ob.get_intake(in_memory_db, "acme")
    assert got2["notes"] == "hot lead" and got2["primary_contact_email"] == "ceo@acme.io"


def test_intake_unknown_field_raises(in_memory_db):
    _draft(in_memory_db)
    with pytest.raises(ValueError):
        ob.upsert_intake(in_memory_db, "acme", bogus="x")


def test_set_manifest_status(in_memory_db):
    _draft(in_memory_db)
    ob.upsert_intake(in_memory_db, "acme")
    ob.set_manifest_status(in_memory_db, "acme", "ready")
    assert ob.get_intake(in_memory_db, "acme")["manifest_status"] == "ready"
    with pytest.raises(ValueError):
        ob.set_manifest_status(in_memory_db, "acme", "bogus")


# --- accounts registry -------------------------------------------------------
def test_account_add_upsert_list_remove(in_memory_db):
    _draft(in_memory_db)
    ob.add_account(in_memory_db, "acme", "twitter", "@acme", "official")
    ob.add_account(in_memory_db, "acme", "twitter", "@founder", "founder", controlled=True, bio="ceo")
    ob.add_account(in_memory_db, "acme", "twitter", "@acme", "team")  # re-add -> upsert
    accts = ob.list_accounts(in_memory_db, "acme")
    assert len(accts) == 2  # not 3 — the natural key dedups
    by_handle = {a["handle"]: a for a in accts}
    assert by_handle["@acme"]["role"] == "team"  # role upserted
    assert by_handle["@founder"]["controlled"] == 1 and by_handle["@founder"]["bio"] == "ceo"
    ctrl = ob.list_accounts(in_memory_db, "acme", controlled_only=True)
    assert [a["handle"] for a in ctrl] == ["@founder"]
    ob.remove_account(in_memory_db, "acme", "twitter", "@acme")
    assert [a["handle"] for a in ob.list_accounts(in_memory_db, "acme")] == ["@founder"]


def test_account_reapply_preserves_optional_metadata(in_memory_db):
    _draft(in_memory_db)
    ob.add_account(
        in_memory_db, "acme", "twitter", "@f", "founder",
        controlled=True, bio="ceo", display_name="Fang",
    )
    ob.add_account(in_memory_db, "acme", "twitter", "@f", "team")  # role correction, no metadata
    a = ob.list_accounts(in_memory_db, "acme")[0]
    assert a["role"] == "team" and a["controlled"] == 0  # role + controlled are replaced
    assert a["bio"] == "ceo" and a["display_name"] == "Fang"  # metadata COALESCE-preserved


# --- docs --------------------------------------------------------------------
def test_docs_add_list_remove(in_memory_db):
    _draft(in_memory_db)
    d1 = ob.add_doc(in_memory_db, "acme", "explainer", "Litepaper", "https://acme.io/lp")
    ob.add_doc(in_memory_db, "acme", "voice", "founder voice", "~/.sable/orgs/acme/voice/f.md")
    assert isinstance(d1, int) and d1 > 0
    assert len(ob.list_docs(in_memory_db, "acme")) == 2
    assert [d["label"] for d in ob.list_docs(in_memory_db, "acme", kind="explainer")] == ["Litepaper"]
    ob.remove_doc(in_memory_db, d1)
    assert [d["kind"] for d in ob.list_docs(in_memory_db, "acme")] == ["voice"]


# --- entitlements ------------------------------------------------------------
def test_entitlement_upsert_list_active_remove(in_memory_db):
    _draft(in_memory_db)
    ob.set_entitlement(in_memory_db, "acme", "reply_assist", tier="standard")
    ob.set_entitlement(in_memory_db, "acme", "checkin", status="paused")
    ob.set_entitlement(  # upsert same service -> update, not duplicate
        in_memory_db, "acme", "reply_assist", tier="premium", config={"cap_usd_week": 5}
    )
    ents = ob.list_entitlements(in_memory_db, "acme")
    assert len(ents) == 2
    ra = next(e for e in ents if e["service_key"] == "reply_assist")
    assert ra["tier"] == "premium" and json.loads(ra["config_json"]) == {"cap_usd_week": 5}
    active = ob.list_entitlements(in_memory_db, "acme", active_only=True)
    assert [e["service_key"] for e in active] == ["reply_assist"]  # paused checkin excluded
    ob.remove_entitlement(in_memory_db, "acme", "reply_assist")
    assert [e["service_key"] for e in ob.list_entitlements(in_memory_db, "acme")] == ["checkin"]


def test_entitlement_invalid_status_raises_in_helper(in_memory_db):
    _draft(in_memory_db)
    with pytest.raises(ValueError):
        ob.set_entitlement(in_memory_db, "acme", "reply_assist", status="bogus")


def test_entitlement_status_check_constraint_in_db(in_memory_db):
    # Defense in depth: the DB CHECK also rejects a bad status (bypassing the helper).
    _draft(in_memory_db)
    with pytest.raises(IntegrityError):
        in_memory_db.execute(
            text("INSERT INTO org_entitlements (org_id, service_key, status) "
                 "VALUES ('acme', 'x', 'bogus')")
        )
