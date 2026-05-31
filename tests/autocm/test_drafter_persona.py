"""C3.3 persona/prompt-cache tests: bimodal NULO prompt + cache_control + mantra.

Covers:
  * the bimodal system blocks (calm vs reactive) differ and carry their register's
    voice instructions;
  * the calibration set is FOLDED INTO the (per-register) cached system prefix;
  * ``cache_control: ephemeral`` is set on the system block of the BUILT request
    (the §6 LLM-seam convention — assert the built request, no live round-trip);
  * ``parse_draft`` parses the JSON draft and degrades to None on bad input;
  * the ``catchphrase_repetition`` :class:`MantraState` (C3.3-owned) — cadence
    DEFERRED by default (operator-driven), repeat-counter advances;
  * the R-4 deterministic fallback renders an in-voice line per register.

No real Anthropic / network. The real adapter's BUILT request is asserted; the
fake provider records calls.
"""
from __future__ import annotations

from sable_platform.autocm.classifier.register import CALM, REACTIVE
from sable_platform.autocm.drafter.persona import (
    DRAFT_MAX_TOKENS,
    DraftRequest,
    MantraState,
    NuloPersona,
    build_cached_request,
    parse_draft,
)
from sable_platform.autocm.llm import AnthropicProvider, DEFAULT_MODEL


# ---------------------------------------------------------------------------
# 1. Bimodal system blocks.
# ---------------------------------------------------------------------------
def test_default_persona_has_distinct_bimodal_blocks() -> None:
    p = NuloPersona.default()
    calm = p.system_block(CALM)
    reactive = p.system_block(REACTIVE)
    assert calm != reactive
    # calm block names the calm register; reactive names the classification-tag rule.
    assert "CALM" in calm and "classification tags" in calm.lower()
    assert "REACTIVE" in reactive and "classification tag" in reactive.lower()


def test_unknown_register_defaults_to_calm_block() -> None:
    p = NuloPersona.default()
    assert p.system_block("nonsense") == p.system_block(CALM)


# ---------------------------------------------------------------------------
# 2. Calibration set folded into the per-register cached prefix.
# ---------------------------------------------------------------------------
class _Spec:
    """Duck-typed PersonaSpec for from_spec (avoids importing the loader)."""

    def __init__(self, *, calm_prompt=None, reactive_prompt=None, calibration_set=None, config=None):
        self.calm_prompt = calm_prompt
        self.reactive_prompt = reactive_prompt
        self.calibration_set = calibration_set or {}
        self.config = config or {}


def test_calibration_examples_folded_into_matching_register_only() -> None:
    spec = _Spec(
        calm_prompt="CALM BASE",
        reactive_prompt="REACTIVE BASE",
        calibration_set={
            "examples": [
                {"register": "calm", "message": "gm", "reply": "gm."},
                {"register": "reactive", "message": "wen moon", "reply": "Statement: no oracle."},
            ]
        },
    )
    p = NuloPersona.from_spec(spec)
    calm = p.system_block(CALM)
    reactive = p.system_block(REACTIVE)
    # calm prefix carries the calm example, NOT the reactive one.
    assert "gm." in calm and "Statement: no oracle." not in calm
    # reactive prefix carries the reactive example, NOT the calm one.
    assert "Statement: no oracle." in reactive and "USER: gm" not in reactive
    # both are folded onto their own base prompt.
    assert calm.startswith("CALM BASE")
    assert reactive.startswith("REACTIVE BASE")


def test_from_spec_falls_back_to_default_block_when_register_prompt_missing() -> None:
    # only calm_prompt provided; reactive falls back to the vendored-derived default.
    spec = _Spec(calm_prompt="ONLY CALM")
    p = NuloPersona.from_spec(spec)
    assert p.system_block(CALM).startswith("ONLY CALM")
    assert "REACTIVE" in p.system_block(REACTIVE)  # default reactive block


# ---------------------------------------------------------------------------
# 3. cache_control: ephemeral on the BUILT request (the mandatory assertion).
# ---------------------------------------------------------------------------
def test_build_cached_request_sets_cache_control_ephemeral_on_system_block() -> None:
    provider = AnthropicProvider()
    persona = NuloPersona.default()
    req = build_cached_request(provider, persona, CALM, "USER PROMPT")

    assert isinstance(req["system"], list)
    assert len(req["system"]) == 1
    block = req["system"][0]
    assert block["type"] == "text"
    assert block["text"] == persona.system_block(CALM)  # the stable cache prefix
    assert block["cache_control"] == {"type": "ephemeral"}
    # the variable user prompt sits AFTER the cached prefix, in the user turn.
    assert req["messages"][0]["role"] == "user"
    assert req["messages"][0]["content"] == "USER PROMPT"
    assert req["max_tokens"] == DRAFT_MAX_TOKENS
    assert req["model"] == DEFAULT_MODEL
    # building must NOT construct the SDK client / hit the network.
    assert provider._client is None


def test_build_cached_request_reactive_uses_reactive_prefix() -> None:
    provider = AnthropicProvider()
    persona = NuloPersona.default()
    req = build_cached_request(provider, persona, REACTIVE, "U")
    assert req["system"][0]["text"] == persona.system_block(REACTIVE)
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# 4. parse_draft — JSON discipline + graceful degradation.
# ---------------------------------------------------------------------------
def test_parse_draft_happy_path() -> None:
    out = parse_draft('{"register": "calm", "draft": "gm.", "reasoning": "greeting"}', register=CALM)
    assert out == ("gm.", "calm", "greeting")


def test_parse_draft_honors_requested_register_when_emitted_invalid() -> None:
    out = parse_draft('{"register": "bogus", "draft": "gm."}', register=CALM)
    assert out == ("gm.", CALM, "")


def test_parse_draft_tolerates_code_fence() -> None:
    out = parse_draft('```json\n{"draft": "gm.", "register": "calm"}\n```', register=CALM)
    assert out is not None and out[0] == "gm."


def test_parse_draft_none_and_empty_and_nonjson_are_none() -> None:
    assert parse_draft(None, register=CALM) is None
    assert parse_draft("", register=CALM) is None
    assert parse_draft("not json at all", register=CALM) is None


def test_parse_draft_no_draft_field_is_none() -> None:
    assert parse_draft('{"register": "calm", "reasoning": "x"}', register=CALM) is None
    assert parse_draft('{"draft": "   "}', register=CALM) is None  # blank draft
    assert parse_draft('["a", "list"]', register=CALM) is None


# ---------------------------------------------------------------------------
# 5. catchphrase_repetition mantra state (C3.3-owned; cadence DEFERRED).
# ---------------------------------------------------------------------------
def test_mantra_default_is_inert_deferred_cadence() -> None:
    m = MantraState()
    assert m.cadence == 0  # deferred auto-drip (operator-driven)
    assert m.should_emit() is False  # no mantra, no cadence


def test_mantra_from_config_loads_state() -> None:
    m = MantraState.from_config(
        {"catchphrase": {"mantra": "the agents are at it.", "cadence": 3, "repeat_count": 2, "since_last": 1}}
    )
    assert m.mantra == "the agents are at it."
    assert m.cadence == 3
    assert m.repeat_count == 2
    assert m.since_last == 1


def test_mantra_from_config_malformed_is_inert() -> None:
    assert MantraState.from_config(None).cadence == 0
    assert MantraState.from_config({"catchphrase": "not a dict"}).mantra is None
    bad = MantraState.from_config({"catchphrase": {"mantra": "x", "cadence": "abc"}})
    assert bad.cadence == 0  # non-int cadence coerced to the inert default


def test_mantra_cadence_off_never_emits_even_with_mantra() -> None:
    """The DEFERRED default: cadence 0 = no auto-drip, even with a configured mantra."""
    m = MantraState(mantra="x", cadence=0, since_last=100)
    assert m.should_emit() is False


def test_mantra_cadence_on_fires_on_beat_and_counter_advances() -> None:
    """When an operator turns the cadence ON, the beat fires once since_last reaches it."""
    m = MantraState(mantra="the agents are at it.", cadence=2, since_last=0)
    assert m.should_emit() is False
    m.record_skip()
    assert m.since_last == 1 and m.should_emit() is False
    m.record_skip()
    assert m.since_last == 2 and m.should_emit() is True
    m.record_emit()  # "repeat until myth" counter advances; window resets.
    assert m.repeat_count == 1 and m.since_last == 0 and m.should_emit() is False


# ---------------------------------------------------------------------------
# 6. R-4 deterministic fallback renders in-voice per register (never empty).
# ---------------------------------------------------------------------------
def test_render_fallback_calm_greeting_non_empty() -> None:
    p = NuloPersona.default()
    req = DraftRequest(client_id=1, text="gm everyone", register=CALM, category="greeting")
    line = p.render_fallback(req)
    assert line and line[0].islower()  # lowercase calm line


def test_render_fallback_reactive_refusal_leads_with_tag() -> None:
    p = NuloPersona.default()
    req = DraftRequest(
        client_id=1, text="wen moon", register=REACTIVE, category="price_prediction", is_refusal=True
    )
    line = p.render_fallback(req)
    assert line.startswith("Statement:") or line.startswith("Refusal:")
