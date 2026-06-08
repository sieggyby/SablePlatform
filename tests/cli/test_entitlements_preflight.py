"""CLI test for `sable-platform entitlements preflight` (P2 safety check)."""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from sable_platform.cli.entitlements_cmds import entitlements
from sable_platform.db import onboarding as ob
from sable_platform.db.connection import get_db


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setenv("SABLE_DB_PATH", str(tmp_path / "sable.db"))
    monkeypatch.setenv("SABLE_OPERATOR_ID", "tester")
    monkeypatch.delenv("SABLE_DATABASE_URL", raising=False)
    monkeypatch.delenv("ENTITLEMENT_ENFORCEMENT", raising=False)
    return lambda *a: CliRunner().invoke(entitlements, list(a))


def _seed(org_id, *, relay_enabled=False, entitlements_=()):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO orgs (org_id, display_name, status, config_json) VALUES (?,?, 'active', '{}')",
            (org_id, org_id),
        )
        if relay_enabled:
            conn.execute("INSERT INTO relay_clients (org_id, enabled) VALUES (?, 1)", (org_id,))
        conn.commit()
        for sk in entitlements_:
            ob.set_entitlement(conn, org_id, sk, status="active")
    finally:
        conn.close()


def test_preflight_flags_relay_org_missing_reply_assist(run):
    _seed("gappy", relay_enabled=True)  # relay-enabled but NO reply_assist entitlement
    res = run("preflight")
    assert res.exit_code == 0
    assert "would be DENIED" in res.output
    assert "gappy" in res.output and "reply_assist" in res.output


def test_preflight_clean_when_entitled(run):
    _seed("good", relay_enabled=True, entitlements_=["reply_assist"])
    res = run("preflight")
    assert res.exit_code == 0
    assert "No coverage gaps" in res.output


def test_preflight_json_reports_flag_state_and_gaps(run):
    _seed("gappy", relay_enabled=True)
    data = json.loads(run("preflight", "--json").output)
    assert data["enforcement_enabled"] is False
    assert data["gaps"] == [{"org_id": "gappy", "missing": ["reply_assist"]}]


def test_preflight_ignores_unonboarded_orgs(run):
    _seed("raw", relay_enabled=False)  # no in-use signal → not a gap
    res = run("preflight")
    assert "No coverage gaps" in res.output
