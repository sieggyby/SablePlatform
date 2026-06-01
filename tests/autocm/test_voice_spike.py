"""C4.2 — Phase -1 NULO voice-spike harness tests.

The harness runs NULO over a fixed pack of >=50 representative messages, drafting
in the classifier-selected register (calm vs reactive) per message via the
PRODUCTION register chooser + composer, and ENFORCES the two-part engineering exit
gate as a REAL assertion (the test asserts ``gate.passed`` — NOT an advisory log
line). It drives the LLM through the C3.1 seam with a DETERMINISTIC FAKE provider
(NO real anthropic, NO network) so it runs in CI; the scorer measures
register-SELECTION + guardrail/voice compliance.

Asserted here (MEGAPLAN C4.2 exit/audit, the auto-checkable engineering gate):
  * pack has >=50 representative messages; the 30-sample "Lex pack" subset exists;
  * the LLM seam is driven via the FAKE (no anthropic / no network);
  * the REAL gate PASSES on the full pack — ``gate.passed is True`` (load-bearing);
  * gate floor (1): ``pass_rate >= 0.75``; floor (2 — NET-NEW): ``min(calm, reactive)
    pass_rate >= 0.60``;
  * NEGATIVE CONTROL: a degraded (off-register) run FAILS the gate (proves the gate
    is real, not advisory);
  * the MEGAPLAN-mandated fixture: a run that clears aggregate 0.75 but has one
    register below 0.60 FAILS the gate;
  * register selection runs through the production ``choose_register`` path;
  * hard-refusal messages produce a refusal signal.
"""
from __future__ import annotations

import inspect

import pytest

from sable_platform.autocm.spike import (
    AGGREGATE_PASS_RATE_FLOOR,
    PER_REGISTER_PASS_RATE_FLOOR,
    FakeSpikeLLMProvider,
    LEX_PACK,
    SPIKE_MESSAGES,
    aggregate_scores,
    evaluate_gate,
    run_spike,
    score_response,
)
from sable_platform.autocm.spike.runner import select_register_for
from sable_platform.autocm.spike.scorer import HARD_REFUSAL_CATEGORIES, score_refusal


# ===========================================================================
# 1. The pack shape (>=50 representative messages + the 30-sample Lex pack).
# ===========================================================================
def test_pack_has_at_least_50_messages() -> None:
    assert len(SPIKE_MESSAGES) >= 50


def test_pack_ids_are_unique() -> None:
    ids = [m.id for m in SPIKE_MESSAGES]
    assert len(ids) == len(set(ids)), "duplicate pack ids"


def test_pack_mines_multiple_clients() -> None:
    """C4.2 scope: mine TIG/SolStitch/Multisynq TG — the pack must span clients."""
    clients = {m.client for m in SPIKE_MESSAGES}
    assert {"RM", "TIG", "SolStitch", "Multisynq"} <= clients


def test_lex_pack_is_exactly_30_rm_samples() -> None:
    """C4.2 scope: produce 30 RM-flavored samples for Lex sign-off."""
    assert len(LEX_PACK) == 30
    assert all(m.client == "RM" for m in LEX_PACK)


def test_lex_pack_is_a_subset_of_the_full_pack() -> None:
    full_ids = {m.id for m in SPIKE_MESSAGES}
    assert all(m.id in full_ids for m in LEX_PACK)


# ===========================================================================
# 2. The harness drives the LLM via the FAKE seam — no anthropic, no network.
# ===========================================================================
def test_harness_drives_llm_through_the_seam_with_fake_provider() -> None:
    """The fake provider's ``complete`` is called (the seam is exercised) and it is a
    deterministic stand-in — NO real anthropic / network. Every recorded call carries
    the prompt-cached system block (the persona system prefix) as the cache prefix."""
    provider = FakeSpikeLLMProvider()
    result = run_spike(provider=provider)
    assert result.provider is provider
    assert len(provider.calls) > 0, "the LLM seam was never driven"
    # the system block (the prompt-cached persona prefix) is passed on every call.
    assert all(call["system"] for call in provider.calls)


def test_fake_provider_module_imports_no_network_or_sdk() -> None:
    """The fake provider's MODULE has no anthropic / httpx / requests / socket IMPORT
    (AST-checked, not substring — the docstring may mention 'anthropic'). CI safety:
    the harness can never make a real LLM / network call."""
    import ast

    from sable_platform.autocm.spike import provider as provider_mod

    tree = ast.parse(inspect.getsource(provider_mod))
    forbidden = {"anthropic", "httpx", "requests", "urllib", "socket", "aiohttp"}
    imported: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    leaked = imported & forbidden
    assert not leaked, f"fake provider module imports forbidden network/SDK deps: {leaked}"


# ===========================================================================
# 3. The REAL gate — the load-bearing C4.2 assertion (gate.passed, not a log line).
# ===========================================================================
def test_full_pack_passes_the_real_gate() -> None:
    """The harness FAILS the build if the pack does not clear the gate — this is the
    REAL assertion the MEGAPLAN C4.2 exit demands, not an advisory log."""
    result = run_spike()
    gate = evaluate_gate(result.aggregate)
    assert gate.passed is True, f"voice-spike gate FAILED: {gate.reasons}"


def test_aggregate_pass_rate_clears_the_075_floor() -> None:
    result = run_spike()
    assert result.aggregate["pass_rate"] >= AGGREGATE_PASS_RATE_FLOOR


def test_per_register_pass_rates_clear_the_060_floor() -> None:
    """NET-NEW C4.2 floor: neither register may collapse while the aggregate clears."""
    agg = run_spike().aggregate
    assert agg["calm_pass_rate"] >= PER_REGISTER_PASS_RATE_FLOOR
    assert agg["reactive_pass_rate"] >= PER_REGISTER_PASS_RATE_FLOOR


def test_gate_floors_are_the_documented_constants() -> None:
    assert AGGREGATE_PASS_RATE_FLOOR == 0.75
    assert PER_REGISTER_PASS_RATE_FLOOR == 0.60


# ===========================================================================
# 4. NEGATIVE CONTROL — the gate is REAL: a degraded run FAILS it.
# ===========================================================================
def test_degraded_run_fails_the_gate() -> None:
    """A provider returning deliberately OFF-register lines (calm-with-tag /
    reactive-without-tag) drives the voice rate down and the gate MUST fail — proving
    the gate is a real assertion, not advisory."""
    result = run_spike(provider=FakeSpikeLLMProvider(degrade=True))
    gate = evaluate_gate(result.aggregate)
    assert gate.passed is False
    assert gate.reasons, "a failing gate must enumerate reasons"


# ===========================================================================
# 5. The MEGAPLAN-mandated fixture: aggregate 0.75 but one register < 0.60 FAILS.
# ===========================================================================
def _synthetic_aggregate(pass_rate: float, calm: float, reactive: float) -> dict:
    """A minimal aggregate dict carrying just the gate inputs (the gate reads these)."""
    return {"pass_rate": pass_rate, "calm_pass_rate": calm, "reactive_pass_rate": reactive}


def test_gate_fails_when_aggregate_clears_but_one_register_below_floor() -> None:
    """C4.2 exit/audit: "Add a fixture asserting a run that passes aggregate 0.75 but
    has one register below 0.60 FAILS the gate." """
    # aggregate clears 0.75, calm is healthy, reactive has collapsed to 0.50 (< 0.60).
    agg = _synthetic_aggregate(pass_rate=0.80, calm=0.95, reactive=0.50)
    gate = evaluate_gate(agg)
    assert gate.passed is False
    assert any("per-register floor" in r for r in gate.reasons)
    assert all("aggregate pass_rate" not in r for r in gate.reasons), (
        "the aggregate cleared — only the per-register floor should be cited"
    )


def test_gate_fails_when_aggregate_below_floor_even_if_registers_ok() -> None:
    agg = _synthetic_aggregate(pass_rate=0.60, calm=0.90, reactive=0.90)
    gate = evaluate_gate(agg)
    assert gate.passed is False
    assert any("aggregate pass_rate" in r for r in gate.reasons)


def test_gate_passes_when_both_floors_cleared() -> None:
    agg = _synthetic_aggregate(pass_rate=0.80, calm=0.75, reactive=0.65)
    gate = evaluate_gate(agg)
    assert gate.passed is True
    assert gate.reasons == []


def test_gate_boundary_exactly_at_floors_passes() -> None:
    """Floors are inclusive (>=): exactly 0.75 / 0.60 PASSES."""
    agg = _synthetic_aggregate(pass_rate=0.75, calm=0.60, reactive=0.60)
    assert evaluate_gate(agg).passed is True


def test_gate_boundary_just_below_register_floor_fails() -> None:
    agg = _synthetic_aggregate(pass_rate=0.90, calm=0.90, reactive=0.59)
    assert evaluate_gate(agg).passed is False


# ===========================================================================
# 6. Register selection runs through the PRODUCTION choose_register path.
# ===========================================================================
def test_register_selection_uses_production_chooser() -> None:
    """Each message's register is the production ``choose_register`` output (registry
    default + charge + modeled LLM register), and lands the expected register for the
    overwhelming majority of the pack (the few honest misses are the conservative
    deterministic charge detector's known limits)."""
    by_id = {m.id: m for m in SPIKE_MESSAGES}
    # a calm-defaulted factual category stays calm
    assert select_register_for(by_id["B1"]) == "calm"  # "what's the price"
    # a reactive-defaulted FUD category goes reactive
    assert select_register_for(by_id["F1"]) == "reactive"  # "this thing is dead"
    # a hard refusal is always reactive
    assert select_register_for(by_id["J1"]) == "reactive"  # "wen moon"
    # an incident-shaped message goes reactive (registry default)
    assert select_register_for(by_id["L1"]) == "reactive"


def test_register_accuracy_is_high_on_the_pack() -> None:
    """register_rate is high — the production chooser lands the curated register for
    nearly the whole pack (a small number of genuine deterministic-charge misses are
    tolerated and surface honestly rather than being papered over)."""
    agg = run_spike().aggregate
    assert agg["register_rate"] >= 0.90


# ===========================================================================
# 7. Hard refusals produce a refusal signal (the most safety-relevant class).
# ===========================================================================
def test_every_hard_refusal_message_produces_a_refusal_signal() -> None:
    """Every hard-refusal-category message in the pack drafts a reply that carries a
    donor refusal signal (the refusal wording never depends on a live LLM — it is the
    calibrated deterministic reactive template)."""
    result = run_spike()
    refusal_scores = [s for s in result.scores if s.category in HARD_REFUSAL_CATEGORIES]
    assert refusal_scores, "the pack must contain hard-refusal messages"
    for s in refusal_scores:
        assert s.draft is not None
        assert score_refusal(s.category, s.draft), (
            f"{s.test_id} ({s.category}) drafted no refusal signal: {s.draft!r}"
        )
        assert s.refusal_correct is True


def test_aggregate_scores_helper_matches_run() -> None:
    """``aggregate_scores`` over the run's scores reproduces the run aggregate (the
    metric producer is the same the gate reads)."""
    result = run_spike()
    recomputed = aggregate_scores(result.scores)
    assert recomputed["pass_rate"] == result.aggregate["pass_rate"]
    assert recomputed["total"] == result.aggregate["total"]


def test_score_response_contract_none_register() -> None:
    """A ``none`` response with a null draft is scored register-correct for a
    none-expected message (the C3.4a strong-skip model)."""
    tc = {"id": "X", "category": "low_content_lol", "expected_register": "none"}
    score = score_response(tc, {"register": "none", "draft": None, "reasoning": "skip"})
    assert score.register_correct is True
    assert score.passed is True
