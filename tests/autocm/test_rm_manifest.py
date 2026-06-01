"""C4.1 — the RobotMoney deployment manifest (config/robotmoney.yaml).

Loads the SHIPPED RM manifest through the C3.1 ``manifest.py`` validator and
asserts the C4.1 invariants:

  * it VALIDATES clean (a real ``DeploymentManifest``, ManifestSecretError-free);
  * the X surface is OFF (NULO v1 is TG-only) — no ``surfaces.x`` block, so the
    manifest carries no X detail to contradict relay's relay-owned ``x.enabled=0``;
  * NO inline secret — every credential is an ``env:`` / ``secret://`` REFERENCE;
  * autonomy starts PAUSED / silent (gate-0), mirroring the seed's
    ``autocm_clients.autonomy_state='paused'`` + ``enabled=0``;
  * the persona + KB references RESOLVE — ``persona.ref: personas/nulo`` points at
    the vendored NULO calm/reactive banks (the SAME banks the seed's NULO persona
    is derived from), and the manifest's six-irreducibles surface area aligns with
    what ``scripts/seed_robotmoney.py`` seeds (no divergence);
  * the C4.1 securities + TG-AI-disclosure controls are RECORDED (disclaimer on
    every market/committee output; the TG fact-of-AI disclosure decision has a
    named owner — closing the SAFETY §8 open item).

NO migration, NO DB writes, NO network: this is pure manifest validation against
the in-tree YAML + the vendored persona banks.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from sable_platform._vendor.sable_pulse_core import (
    NULO_CALM,
    NULO_REACTIVE,
    load_nulo,
    persona_data_dir,
)
from sable_platform.autocm.drafter.persona import NuloPersona
from sable_platform.autocm.manifest import (
    DeploymentManifest,
    ManifestSecretError,
    load_manifest,
)

# Repo-root-relative path to the shipped manifest (tests/autocm/ -> repo root).
_REPO_ROOT = Path(__file__).resolve().parents[2]
RM_MANIFEST_PATH = _REPO_ROOT / "config" / "robotmoney.yaml"

# The relay-owned surface flags as the RM seed seeds them (relay_clients.config):
# telegram ON, x OFF, discord OFF. The manifest READS these; it never re-declares.
SEEDED_RELAY_FLAGS = {"tg": True, "x": False, "discord": False}


@pytest.fixture(scope="module")
def rm_manifest() -> DeploymentManifest:
    """The shipped RM manifest, loaded once through the real validator."""
    raw = RM_MANIFEST_PATH.read_text()
    return load_manifest(raw)


# ---------------------------------------------------------------------------
# (1) validates clean
# ---------------------------------------------------------------------------
def test_rm_manifest_file_exists() -> None:
    assert RM_MANIFEST_PATH.is_file(), f"missing RM manifest at {RM_MANIFEST_PATH}"


def test_rm_manifest_validates_clean(rm_manifest: DeploymentManifest) -> None:
    # load_manifest raised neither ManifestSecretError nor ValidationError.
    assert isinstance(rm_manifest, DeploymentManifest)
    assert rm_manifest.client.id == "robotmoney"
    assert rm_manifest.client.display_name == "RobotMoney"


# ---------------------------------------------------------------------------
# (2) X surface OFF — TG-only; no manifest-side X detail to contradict relay
# ---------------------------------------------------------------------------
def test_rm_manifest_has_no_x_surface(rm_manifest: DeploymentManifest) -> None:
    # NULO v1 is TG-only: there is NO surfaces.x block, only TG.
    assert rm_manifest.surfaces.x is None, "v1 is TG-only — the manifest must not declare an X surface"
    assert rm_manifest.surfaces.tg is not None, "the TG surface must be declared"


def test_rm_manifest_x_disabled_and_noncontradictory_with_relay(
    rm_manifest: DeploymentManifest,
) -> None:
    # Relay owns enablement (x.enabled=0 in the seed). Because the manifest carries
    # NO X detail, it cannot contradict relay — the convergence test for tension #6.
    contradictions = rm_manifest.surfaces_contradict_relay(SEEDED_RELAY_FLAGS)
    assert contradictions == [], (
        f"manifest declares detail for surfaces relay hasn't enabled: {contradictions}"
    )
    # And X is genuinely OFF on the relay side (the single source of truth).
    assert SEEDED_RELAY_FLAGS["x"] is False


def test_rm_manifest_does_not_redeclare_surface_enablement(
    rm_manifest: DeploymentManifest,
) -> None:
    # tension #6: the manifest never re-declares surfaces.{tg,x,discord}.enabled.
    assert not hasattr(rm_manifest.surfaces.tg, "enabled")


# ---------------------------------------------------------------------------
# (3) no inline secret — every credential is a reference
# ---------------------------------------------------------------------------
def test_rm_manifest_credentials_are_references(rm_manifest: DeploymentManifest) -> None:
    assert rm_manifest.surfaces.tg.bot_account == "env:RM_TG_BOT"
    assert rm_manifest.surfaces.tg.chat_id == "env:RM_TG_COMMUNITY_CHAT_ID"
    assert rm_manifest.llm.api_key_ref == "secret://anthropic/rm"
    assert rm_manifest.client.escalation_channel == "env:RM_OPERATOR_CHAT_ID"


def test_rm_manifest_rejects_inline_secret_if_introduced() -> None:
    # Defensive: prove the validator would REJECT the manifest if a real bot token
    # were ever pasted inline (the secrets-as-references invariant is live on the
    # shipped file, not just the C3.1 fixture).
    raw = RM_MANIFEST_PATH.read_text()
    bad = raw.replace(
        "bot_account: env:RM_TG_BOT",
        "bot_account: 7123456789:AAH0fakeBotTokenLooksLikeThisLongString",
    )
    assert bad != raw, "replacement sentinel not found — test would be vacuous"
    with pytest.raises(ManifestSecretError):
        load_manifest(bad)


# ---------------------------------------------------------------------------
# (4) autonomy starts PAUSED / silent (gate-0)
# ---------------------------------------------------------------------------
def test_rm_manifest_autonomy_starts_paused(rm_manifest: DeploymentManifest) -> None:
    autonomy = rm_manifest.ops.get("autonomy", {})
    assert autonomy.get("initial_state") == "paused", "autonomy must start PAUSED (gate-0)"
    assert autonomy.get("silent") is True, "v1 starts silent"
    assert autonomy.get("enabled") is False, "tenant ships dormant (enabled=0), matching the seed"


def test_rm_manifest_launch_gates_open_disclaimer_locked(
    rm_manifest: DeploymentManifest,
) -> None:
    # Mirror the seed's surface_config.ops.launch_gates: persona/ai-disclosure OPEN
    # (await Lex), securities disclaimer always ON.
    gates = rm_manifest.ops.get("launch_gates", {})
    assert gates.get("persona_greenlit") is False
    assert gates.get("ai_disclosure") is False
    assert gates.get("securities_disclaimer") is True


# ---------------------------------------------------------------------------
# (5) persona + KB references resolve (point at what the seed seeds)
# ---------------------------------------------------------------------------
def test_rm_manifest_persona_ref_resolves_to_nulo_banks(
    rm_manifest: DeploymentManifest,
) -> None:
    # persona.ref 'personas/nulo' -> the vendored robotmoney/nulo calm+reactive banks.
    assert rm_manifest.persona.ref == "personas/nulo"
    nulo_dir = persona_data_dir("robotmoney", "nulo")
    assert nulo_dir.is_dir(), f"vendored NULO banks missing at {nulo_dir}"
    banks = load_nulo(nulo_dir)
    # Both registers present — the bimodal persona bank is FINALIZED (calm+reactive).
    assert NULO_CALM in banks, "calm register bank missing"
    assert NULO_REACTIVE in banks, "reactive register bank missing"


def test_rm_manifest_persona_builds_both_registers() -> None:
    # The default NULO persona (what an unseeded client gets, derived from the SAME
    # banks the manifest references) composes BOTH register system blocks.
    persona = NuloPersona.default()
    assert persona.calm_prompt, "calm register system block must be non-empty"
    assert persona.reactive_prompt, "reactive register system block must be non-empty"
    assert persona.system_block("calm"), "calm system_block must compose"
    assert persona.system_block("reactive"), "reactive system_block must compose"


def test_rm_manifest_kb_sources_present_and_deferred(
    rm_manifest: DeploymentManifest,
) -> None:
    # The Q14 baseline vector-KB source list is present but DEFERRED (kb_config
    # vector_kb_enabled=False in the seed; the C3.4+ refresher owns the live fetch).
    types = {s.get("type") for s in rm_manifest.kb_sources}
    assert {"website", "committee", "on_chain"} <= types
    for src in rm_manifest.kb_sources:
        assert src.get("deferred") is True, f"baseline KB source must be deferred: {src}"


# ---------------------------------------------------------------------------
# Alignment with scripts/seed_robotmoney.py (no divergent parallel seed)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def seed_module():
    """Import the existing RM seed module to assert the manifest aligns with it."""
    spec = importlib.util.spec_from_file_location(
        "seed_robotmoney_for_test", _REPO_ROOT / "scripts" / "seed_robotmoney.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_aligns_with_seed_no_divergence(
    rm_manifest: DeploymentManifest, seed_module
) -> None:
    # Persona: the seed writes a persona named NULO; the manifest references the
    # banks NULO is derived from.
    assert seed_module.PERSONA_NAME == "NULO"

    # Surface set: seed seeds TG on / X off / Discord off; manifest declares TG only.
    assert seed_module.RELAY_CONFIG["surfaces"]["telegram"]["enabled"] is True
    assert seed_module.RELAY_CONFIG["surfaces"]["x"]["enabled"] is False
    assert rm_manifest.surfaces.tg is not None
    assert rm_manifest.surfaces.x is None

    # Autonomy posture: seed paused/dormant; manifest autonomy paused.
    assert rm_manifest.ops["autonomy"]["initial_state"] == "paused"

    # KB irreducibles the seed writes are reflected in the manifest's source URLs.
    seed_consts = {k: v for k, v, _ in seed_module.KB_CONSTANTS}
    src_urls = {s.get("url") for s in rm_manifest.kb_sources}
    assert seed_consts["website"] in src_urls
    assert seed_consts["committee_url"] in src_urls

    # Operators + on-call match the seed.
    assert rm_manifest.ops["hidden_operators"] == seed_module.OPERATORS
    assert rm_manifest.ops["oncall_primary"] == seed_module.ONCALL_PRIMARY

    # Escalation founder matches the seed (handle modulo a leading @).
    seed_founder = seed_module.SURFACE_CONFIG["ops"]["escalation"]["founder_handle"].lstrip("@")
    assert rm_manifest.client.founder_handle.lstrip("@") == seed_founder


# ---------------------------------------------------------------------------
# C4.1 securities + TG-AI-disclosure controls are RECORDED
# ---------------------------------------------------------------------------
def test_rm_manifest_securities_disclaimer_invariant(
    rm_manifest: DeploymentManifest,
) -> None:
    sec = rm_manifest.ops.get("securities", {})
    # Disclaimer mandatory on every market/committee output (R-6 / AGENTS §86/§98).
    assert set(sec.get("disclaimer_required_on", [])) >= {"market", "committee"}
    assert sec.get("disclaimer"), "a disclaimer string must be present"
    assert sec.get("no_recommendation_shape") is True
    # Hard refusals are recorded and (by spec) never overridden.
    refusals = set(sec.get("hard_refusals_never_overridden", []))
    assert {"price_prediction", "financial_advice", "legal_regulatory", "personal_portfolio"} <= refusals


def test_rm_manifest_tg_ai_disclosure_decision_is_owned(
    rm_manifest: DeploymentManifest,
) -> None:
    # SAFETY §8 closed: the TG fact-of-AI disclosure decision is RECORDED with a
    # named owner (Lex), not left unowned.
    disc = rm_manifest.ops.get("tg_ai_disclosure", {})
    assert disc.get("owner") == "lex", "the TG AI-disclosure decision must have a named owner"
    assert disc.get("bot_bio_line"), "a TG bot-bio / pinned disclosure line must be specified"
    # The 'what-the-bot-won't-do' page decision is made (owned), not silently dropped.
    page = disc.get("what_the_bot_wont_do_page", {})
    assert page.get("owner") == "lex"
    assert page.get("decision"), "the what-the-bot-won't-do page decision must be recorded"
