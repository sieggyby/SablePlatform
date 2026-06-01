"""C3.1 seam #3: deployment-manifest schema + secrets invariant + convergence.

  * the manifest (PRODUCTIZATION §3 shape) VALIDATES against a test client.
  * the secrets-as-references invariant REJECTS an inline secret value and ACCEPTS
    an env:/secret:// handle.
  * config-schema convergence (tension #6): an org's relay surface flags + the
    autocm manifest are non-contradictory — a single source of truth for
    ``surfaces.x.enabled`` (manifest READS relay flags, does not duplicate them).
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from sable_platform.autocm.manifest import (
    DeploymentManifest,
    ManifestSecretError,
    load_manifest,
)

# A PRODUCTIZATION §3-shaped manifest for a test client — credential fields are
# REFERENCES (env:/secret://), never inline secrets.
TEST_CLIENT_MANIFEST = """
client:
  id: robotmoney
  display_name: RobotMoney
  founder_handle: lex_sokolin
  escalation_channel: tg_dm

persona:
  ref: personas/nulo

surfaces:
  tg:
    chat_id: "-1001234567890"
    bot_account: env:RM_TG_BOT
  x:
    project_handle: RobotMoneyAgent
    oauth_grant_ref: env:RM_X_OAUTH
    surfaces:
      - replies_on_own_tweets
      - mentions
    surfaces_excluded:
      - quote_tweets
    enabled_at: 2026-W4
  discord: {}

kb_sources:
  - type: website
    url: https://robotmoney.net
    refresh: weekly
  - type: on_chain
    adapter: base_alchemy
    queries: [vault_tvl, last_buyback]

categories:
  greeting:
    initial_state: hitl
    threshold: 0.70
  price:
    initial_state: hitl
    threshold: 0.85
  trust:
    initial_state: hitl
    threshold: 0.92

llm:
  provider: anthropic
  model: claude-opus-4-8
  api_key_ref: secret://anthropic/rm

ops:
  hidden_operators: [sieggy]
  digest:
    cadence: weekly_monday
    timezone: America/New_York
    deliver_to: tg_dm:lex_sokolin
    preview_to: operator_chat
    auto_deliver_from: week 5
"""


def test_manifest_validates_against_test_client() -> None:
    m = load_manifest(TEST_CLIENT_MANIFEST)
    assert isinstance(m, DeploymentManifest)
    assert m.client.id == "robotmoney"
    assert m.persona.ref == "personas/nulo"
    assert m.surfaces.tg is not None and m.surfaces.tg.chat_id == "-1001234567890"
    assert m.surfaces.x is not None and m.surfaces.x.project_handle == "RobotMoneyAgent"
    assert set(m.categories) == {"greeting", "price", "trust"}
    assert m.categories["trust"].threshold == 0.92
    assert m.llm.provider == "anthropic"


def test_manifest_credential_refs_are_references_not_inline() -> None:
    m = load_manifest(TEST_CLIENT_MANIFEST)
    assert m.surfaces.tg.bot_account == "env:RM_TG_BOT"
    assert m.surfaces.x.oauth_grant_ref == "env:RM_X_OAUTH"
    assert m.llm.api_key_ref == "secret://anthropic/rm"


def test_manifest_rejects_inline_oauth_secret() -> None:
    bad = TEST_CLIENT_MANIFEST.replace(
        "oauth_grant_ref: env:RM_X_OAUTH",
        "oauth_grant_ref: ya29.A0ARrdaInLiNeReALtOkEnVaLuE1234567890",
    )
    with pytest.raises(ManifestSecretError):
        load_manifest(bad)


def test_manifest_rejects_placeholder_secret() -> None:
    # the §3 example's `<secret>` placeholder must be rejected (model the ref form).
    bad = TEST_CLIENT_MANIFEST.replace("oauth_grant_ref: env:RM_X_OAUTH", "oauth_grant_ref: <secret>")
    with pytest.raises(ManifestSecretError):
        load_manifest(bad)


def test_manifest_rejects_inline_bot_token() -> None:
    bad = TEST_CLIENT_MANIFEST.replace(
        "bot_account: env:RM_TG_BOT",
        "bot_account: 7123456789:AAH0fakeBotTokenLooksLikeThisLongString",
    )
    with pytest.raises(ManifestSecretError):
        load_manifest(bad)


def test_manifest_accepts_secretstore_handle() -> None:
    ok = TEST_CLIENT_MANIFEST.replace("oauth_grant_ref: env:RM_X_OAUTH", "oauth_grant_ref: secret://x/rm")
    m = load_manifest(ok)
    assert m.surfaces.x.oauth_grant_ref == "secret://x/rm"


def test_manifest_surface_block_has_no_enabled_field() -> None:
    # tension #6: enablement is relay-owned; a manifest `enabled` is ignored.
    raw = TEST_CLIENT_MANIFEST.replace(
        "  tg:\n    chat_id:", "  tg:\n    enabled: true\n    chat_id:"
    )
    m = load_manifest(raw)
    # extra="ignore" drops it — there is no manifest-side `enabled` to contradict relay.
    assert not hasattr(m.surfaces.tg, "enabled")


# ---------------------------------------------------------------------------
# Config-schema convergence (tension #6) — single source of truth for enablement
# ---------------------------------------------------------------------------
def _seed_relay_client_with_surfaces(conn, org_id: str, surfaces: dict) -> None:
    conn.execute(
        text(
            "INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"
        ),
        {"o": org_id},
    )
    conn.execute(
        text(
            "INSERT INTO relay_clients (org_id, enabled, config) VALUES (:o, 1, :cfg)"
        ),
        {"o": org_id, "cfg": json.dumps({"surfaces": surfaces})},
    )


def _relay_surface_flags(conn, org_id: str) -> dict:
    """Read relay_clients.config and project the surfaces.*.enabled flags (relay-owned)."""
    from sable_platform.relay import db as relay_db

    raw = relay_db.read_client_config(conn, org_id)
    cfg = json.loads(raw) if raw else {}
    surfaces = cfg.get("surfaces", {})
    return {name: bool(s.get("enabled", False)) for name, s in surfaces.items()}


def test_manifest_and_relay_config_are_noncontradictory(sa_conn) -> None:
    org_id = "org_rm"
    # relay owns enablement: tg + x ON, discord OFF.
    _seed_relay_client_with_surfaces(
        sa_conn,
        org_id,
        {"tg": {"enabled": True}, "x": {"enabled": True}, "discord": {"enabled": False}},
    )
    sa_conn.commit()

    # manifest carries DETAIL only for tg + x (the enabled surfaces), discord block absent.
    manifest = load_manifest(TEST_CLIENT_MANIFEST.replace("  discord: {}\n", ""))
    flags = _relay_surface_flags(sa_conn, org_id)

    contradictions = manifest.surfaces_contradict_relay(flags)
    assert contradictions == [], (
        f"manifest declares detail for surfaces relay hasn't enabled: {contradictions}"
    )


def test_manifest_contradiction_detected_when_surface_disabled(sa_conn) -> None:
    org_id = "org_rm2"
    # relay says x is DISABLED, but the manifest still carries an x surface block.
    _seed_relay_client_with_surfaces(
        sa_conn, org_id, {"tg": {"enabled": True}, "x": {"enabled": False}}
    )
    sa_conn.commit()

    manifest = load_manifest(TEST_CLIENT_MANIFEST.replace("  discord: {}\n", ""))
    flags = _relay_surface_flags(sa_conn, org_id)
    contradictions = manifest.surfaces_contradict_relay(flags)
    # x is declared in the manifest but relay reports it disabled → contradiction.
    assert "x" in contradictions
    # tg is enabled in relay AND declared in the manifest → NOT a contradiction.
    assert "tg" not in contradictions
