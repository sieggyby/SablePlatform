"""CLI tests for `sable-platform onboard …` (Chunk 3-4). File-backed sable.db so each
command opens/closes its own connection (matching production); tmp SABLE_HOME for scaffold.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from sable_platform.cli.onboarding_cmds import onboard, operator
from sable_platform.db.connection import get_db


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SABLE_DB_PATH", str(tmp_path / "sable.db"))
    monkeypatch.setenv("SABLE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SABLE_OPERATOR_ID", "tester")
    monkeypatch.delenv("SABLE_DATABASE_URL", raising=False)
    return tmp_path


@pytest.fixture
def run(env):
    runner = CliRunner()
    return lambda *args: runner.invoke(onboard, list(args))


def _org(org_id="acme"):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT status, config_json, twitter_handle, discord_server_id FROM orgs WHERE org_id=?",
            (org_id,),
        ).fetchone()
        return dict(row._mapping) if row else None
    finally:
        conn.close()


def test_init_creates_draft_org_intake_and_scaffold(run, env):
    res = run("init", "acme", "--name", "Acme")
    assert res.exit_code == 0, res.output
    org = _org()
    assert org["status"] == "inactive"  # draft
    assert json.loads(org["config_json"])["org_type"] == "client"
    # scaffold landed under SABLE_HOME/orgs/acme
    org_dir = env / "home" / "orgs" / "acme"
    assert (org_dir / "brief.md").is_file() and (org_dir / "guardrails.yaml").is_file()
    # docs registered
    assert "Scaffolded" in res.output


def test_set_intake_and_config_with_validation(run):
    run("init", "acme", "--name", "Acme")
    assert run("set", "acme", "primary_contact_email", "ceo@acme.io").exit_code == 0
    assert run("set", "acme", "sector", "DeFi").exit_code == 0
    bad = run("set", "acme", "sector", "Nonsense")
    assert bad.exit_code == 1 and "Invalid sector" in bad.output
    unknown = run("set", "acme", "bogus_field", "x")
    assert unknown.exit_code == 1 and "Unknown field" in unknown.output
    assert json.loads(_org()["config_json"])["sector"] == "DeFi"


def test_account_add_list(run):
    run("init", "acme", "--name", "Acme")
    r = run("account", "add", "acme", "--platform", "twitter", "--handle", "@Acme", "--role", "official")
    assert r.exit_code == 0
    run("account", "add", "acme", "--platform", "twitter", "--handle", "@founder", "--role", "founder", "--controlled")
    out = run("account", "list", "acme").output
    assert "@Acme" in out and "@founder" in out and "★" in out


def test_status_blocking_then_ready_and_exit_codes(run):
    run("init", "acme", "--name", "Acme")
    run("service", "add", "acme", "kol")  # kol requires only a twitter handle
    blocked = run("status", "acme")
    assert blocked.exit_code == 1  # twitter missing -> blocking
    assert "❌" in blocked.output and "Twitter handle" in blocked.output
    run("account", "add", "acme", "--platform", "twitter", "--handle", "@Acme", "--role", "official")
    ready = run("status", "acme")
    assert ready.exit_code == 0  # now satisfied
    assert "no blocking items" in ready.output


def test_apply_blocks_without_inputs_then_activates_and_projects(run):
    run("init", "acme", "--name", "Acme")
    run("service", "add", "acme", "kol")
    blocked = run("apply", "acme")
    assert blocked.exit_code == 1 and "blocking inputs missing" in blocked.output
    assert _org()["status"] == "inactive"  # not activated while blocked
    # satisfy + apply
    run("account", "add", "acme", "--platform", "twitter", "--handle", "@Acme", "--role", "official")
    run("account", "add", "acme", "--platform", "discord", "--handle", "999", "--role", "community")
    res = run("apply", "acme")
    assert res.exit_code == 0, res.output
    org = _org()
    assert org["status"] == "active"  # go-live flip
    assert org["twitter_handle"] == "@Acme"  # projected from the registry (SSOT)
    assert org["discord_server_id"] == "999"
    assert "Remaining cross-repo provisioning" in res.output  # checklist emitted


def test_apply_force_overrides_blocking(run):
    run("init", "acme", "--name", "Acme")
    run("service", "add", "acme", "kol")  # twitter missing
    res = run("apply", "acme", "--force")
    assert res.exit_code == 0 and _org()["status"] == "active"


def test_apply_dry_run_writes_nothing(run):
    run("init", "acme", "--name", "Acme")
    run("service", "add", "acme", "kol")
    run("account", "add", "acme", "--platform", "twitter", "--handle", "@Acme", "--role", "official")
    res = run("apply", "acme", "--dry-run")
    assert res.exit_code == 0 and "[dry-run]" in res.output
    assert _org()["status"] == "inactive"  # unchanged


def test_activate_flips_status(run):
    run("init", "acme", "--name", "Acme")
    assert _org()["status"] == "inactive"
    res = run("activate", "acme")
    assert res.exit_code == 0 and _org()["status"] == "active"


def test_from_prospect_carries_over_handles(run):
    # seed a prospect-shaped org (twitter on the column, guild in config_json) like sable-audit
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO orgs (org_id, display_name, twitter_handle, status, config_json) "
            "VALUES ('prospY', 'ProspY', '@ProspHandle', 'inactive', ?)",
            (json.dumps({"org_type": "prospect", "discord_guild_id": "777"}),),
        )
        conn.commit()
    finally:
        conn.close()
    res = run("init", "prospY", "--name", "ProspY", "--from-prospect")
    assert res.exit_code == 0 and "Carried over" in res.output
    accts = run("account", "list", "prospY").output
    assert "@ProspHandle" in accts and "777" in accts
    # and the carried-over twitter satisfies a twitter-needing service (no re-entry)
    run("service", "add", "prospY", "kol")
    assert run("status", "prospY").exit_code == 0  # not blocked — handle came from the prospect


def test_rm_commands_reject_nonexistent_org(run):
    assert run("account", "rm", "ghost", "--platform", "twitter", "--handle", "@x").exit_code == 1
    assert run("service", "rm", "ghost", "kol").exit_code == 1
    assert run("doc", "rm", "ghost", "1").exit_code == 1


def test_doc_and_service_lifecycle(run):
    run("init", "acme", "--name", "Acme")
    add = run("doc", "add", "acme", "--kind", "explainer", "--label", "LP", "--location", "https://x")
    assert add.exit_code == 0
    assert "LP" in run("doc", "list", "acme").output
    run("service", "add", "acme", "reply_assist", "--status", "trial")
    assert "reply_assist" in run("service", "list", "acme").output
    assert run("service", "rm", "acme", "reply_assist").exit_code == 0
    assert "reply_assist" not in run("service", "list", "acme").output


def test_operator_checklist_emits_grants(env):
    res = CliRunner().invoke(operator, [
        "checklist", "new_op", "--email", "op@sable.io", "--role", "operator",
        "--orgs", "tig,solstitch", "--persona", "@tigintern", "--compose-as", "@tigfoundation",
    ])
    assert res.exit_code == 0
    assert '"op@sable.io"' in res.output and '"operatorId": "new_op"' in res.output
    assert '"assignedOrgs": ["tig", "solstitch"]' in res.output
    assert "export SABLE_OPERATOR_ID=new_op" in res.output
    assert "@tigintern" in res.output and "@tigfoundation" in res.output


def test_status_json_shape(run):
    run("init", "acme", "--name", "Acme")
    run("service", "add", "acme", "kol")
    res = run("status", "acme", "--json")
    data = json.loads(res.output)
    assert data["org_id"] == "acme" and data["is_ready"] is False
    assert "twitter_handle" in data["blocking"]
