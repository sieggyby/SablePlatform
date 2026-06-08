"""Tests for the entitlement-enforcement gate (ONBOARDING_PHASE2_PLAN.md P2).

Pins the double-guard truth table + fail-open + the pure pass-through filter. The gate is
DORMANT by default (flag off → always allow), so these flip ENTITLEMENT_ENFORCEMENT per case.
"""
from __future__ import annotations

import pytest

from sable_platform.db import onboarding as ob
from sable_platform.db.entitlements import filter_entitled, has_entitlement
from sable_platform.db.orgs import upsert_client_org


def _org(conn, org_id="acme"):
    upsert_client_org(conn, org_id=org_id, display_name=org_id, status="active")
    return org_id


def test_flag_off_always_allows(in_memory_db, monkeypatch):
    monkeypatch.delenv("ENTITLEMENT_ENFORCEMENT", raising=False)
    _org(in_memory_db)
    # even an onboarded org missing the sku is allowed while the flag is off
    ob.set_entitlement(in_memory_db, "acme", "tracking")
    assert has_entitlement(in_memory_db, "acme", "reply_assist") is True


def test_flag_on_zero_active_rows_allows(in_memory_db, monkeypatch):
    monkeypatch.setenv("ENTITLEMENT_ENFORCEMENT", "true")
    _org(in_memory_db)  # un-onboarded: no entitlement rows at all
    assert has_entitlement(in_memory_db, "acme", "reply_assist") is True


def test_flag_on_only_paused_rows_allows(in_memory_db, monkeypatch):
    monkeypatch.setenv("ENTITLEMENT_ENFORCEMENT", "true")
    _org(in_memory_db)
    ob.set_entitlement(in_memory_db, "acme", "reply_assist", status="paused")  # 0 ACTIVE rows
    assert has_entitlement(in_memory_db, "acme", "reply_assist") is True  # de-onboarded → safe-allow


def test_flag_on_onboarded_with_sku_allows(in_memory_db, monkeypatch):
    monkeypatch.setenv("ENTITLEMENT_ENFORCEMENT", "1")
    _org(in_memory_db)
    ob.set_entitlement(in_memory_db, "acme", "reply_assist", status="active")
    assert has_entitlement(in_memory_db, "acme", "reply_assist") is True


def test_flag_on_onboarded_missing_sku_denies(in_memory_db, monkeypatch):
    monkeypatch.setenv("ENTITLEMENT_ENFORCEMENT", "true")
    _org(in_memory_db)
    ob.set_entitlement(in_memory_db, "acme", "tracking", status="active")  # ≥1 active, but not reply_assist
    assert has_entitlement(in_memory_db, "acme", "reply_assist") is False  # the ONLY deny path
    # a paused target SKU on an otherwise-onboarded org also denies
    ob.set_entitlement(in_memory_db, "acme", "reply_assist", status="paused")
    assert has_entitlement(in_memory_db, "acme", "reply_assist") is False


def test_fail_open_on_error(in_memory_db, monkeypatch):
    # a broken list_entitlements (e.g. missing table) must ALLOW, never starve a client
    monkeypatch.setenv("ENTITLEMENT_ENFORCEMENT", "true")
    monkeypatch.setattr(
        "sable_platform.db.onboarding.list_entitlements",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no table")),
    )
    assert has_entitlement(in_memory_db, "acme", "reply_assist") is True


def test_filter_entitled_passthrough_when_off(in_memory_db, monkeypatch):
    monkeypatch.delenv("ENTITLEMENT_ENFORCEMENT", raising=False)
    # flag off → verbatim, even for orgs that don't exist (pure pass-through, no DB touch)
    assert filter_entitled(in_memory_db, ["a", "b", "c"], "reply_assist") == ["a", "b", "c"]


def test_filter_entitled_filters_when_on(in_memory_db, monkeypatch):
    monkeypatch.setenv("ENTITLEMENT_ENFORCEMENT", "true")
    _org(in_memory_db, "yes")
    ob.set_entitlement(in_memory_db, "yes", "reply_assist", status="active")
    _org(in_memory_db, "no")
    ob.set_entitlement(in_memory_db, "no", "tracking", status="active")  # onboarded, not reply_assist
    _org(in_memory_db, "unonboarded")  # 0 rows → allowed
    out = filter_entitled(in_memory_db, ["yes", "no", "unonboarded"], "reply_assist")
    assert out == ["yes", "unonboarded"]  # "no" filtered out; un-onboarded kept (safe)
