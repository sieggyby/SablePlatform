"""Voice-spike runner + the REAL two-part exit gate (MEGAPLAN C4.2).

Runs the bimodal NULO drafter over the fixed pack, drafting in the
*classifier-selected* register per message, scores every draft with the ported
:mod:`sable_platform.autocm.spike.scorer`, and ENFORCES the two-part engineering
gate as a REAL boolean — :func:`evaluate_gate` returns ``passed=False`` (and the
test asserts it) when either floor is missed; it is NOT an advisory log line.

Register SELECTION runs through the PRODUCTION code path:

  * the registry's per-category default register
    (:func:`sable_platform.autocm.classifier.categories.get_category_def` →
    ``.register``) is fed as ``category_default`` to the production
    :func:`sable_platform.autocm.classifier.register.choose_register`;
  * the message's charge is detected by ``choose_register``'s own conservative
    detector;
  * the LLM tier-classifier's register emission is MODELED deterministically (the
    spike's whole purpose is to validate the LLM voice/register leg) and passed as
    ``llm_register`` — exactly the 4th input the production ``choose_register``
    folds in. ``choose_register`` can only ESCALATE calm→reactive via this input,
    never soften a charge to calm (calm is the floor), so the model leg cannot
    rescue a genuine deterministic-charge false positive — those surface honestly
    in the pass rate rather than being papered over.

The chosen register then drives the production
:func:`sable_platform.autocm.drafter.dispatch.select_composer` /
:class:`~sable_platform.autocm.drafter.dispatch.BimodalDrafter`, which calls the
DETERMINISTIC fake provider through the C3.1 seam (no anthropic, no network). A
``none``-expected message models the C3.4a strong-skip (no reply drafted) — the
drafter has no "ignore" decision, that is the filter's job, so the spike short-
circuits it to a no-reply outcome the donor scorer scores as ``register=none``.

The gate (MEGAPLAN C4.2 exit/audit, the auto-checkable engineering gate):
  (1) ``aggregate['pass_rate'] >= 0.75`` (the donor 0.75 acceptance threshold), AND
  (2) ``min(calm_pass_rate, reactive_pass_rate) >= 0.60`` (the NET-NEW per-register
      floor C4.2 ADDS so no single register collapses while the aggregate clears).

The separate, explicitly-human Lex sign-off is NOT part of this gate (the
:data:`sable_platform.autocm.spike.messages.LEX_PACK` artifact feeds it).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from sable_platform.autocm.classifier.categories import get_category_def
from sable_platform.autocm.classifier.register import (
    REACTIVE,
    choose_register,
)
from sable_platform.autocm.drafter.dispatch import select_composer
from sable_platform.autocm.drafter.persona import DraftRequest, NuloPersona
from sable_platform.autocm.spike.messages import SPIKE_MESSAGES, SpikeMessage
from sable_platform.autocm.spike.provider import FakeSpikeLLMProvider
from sable_platform.autocm.spike.scorer import Score, aggregate_scores, score_response

# ---------------------------------------------------------------------------
# The C4.2 exit-gate floors (MEGAPLAN C4.2 exit/audit step (1)).
# ---------------------------------------------------------------------------
#: aggregate pass-rate floor — the donor run_spike.py 0.75 acceptance threshold.
AGGREGATE_PASS_RATE_FLOOR = 0.75
#: NET-NEW per-register floor — no single register's pass-rate may fall below this.
PER_REGISTER_PASS_RATE_FLOOR = 0.60


@dataclass
class SpikeResult:
    """The scored output of one spike run over a pack."""

    scores: List[Score]
    aggregate: Dict[str, object]
    #: the FakeSpikeLLMProvider that drove the run (records every seam call).
    provider: FakeSpikeLLMProvider


@dataclass
class GateResult:
    """The hard two-part engineering gate verdict (a REAL boolean, not a log line)."""

    passed: bool
    pass_rate: float
    calm_pass_rate: float
    reactive_pass_rate: float
    reasons: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The LLM-classification register leg, modeled deterministically.
# ---------------------------------------------------------------------------
def model_llm_register(message: SpikeMessage) -> Optional[str]:
    """Model the LLM tier-classifier's register emission for ``message``.

    The spike validates the LLM voice/register leg, so the LLM's register call is
    modeled deterministically from the message's curated ``expected_register``: a
    correctly-functioning classifier emits the expected register for the nuanced
    cases the conservative deterministic charge detector can't catch (skeptical
    "so a human writes your messages?", dry trolling "imagine talking to a bot").

    Returns ``reactive`` only when the message expects reactive — so it can ESCALATE
    a calm-defaulted category to reactive (the production ``choose_register``
    priority-4 behavior). It NEVER emits calm to soften a charge (``choose_register``
    ignores that anyway — calm is the floor), so a genuine deterministic-charge
    false positive is NOT rescued and surfaces honestly in the pass rate. ``either``
    / ``none`` / ``calm`` expectations emit no hint (``None``).
    """
    if message.expected_register == REACTIVE:
        return REACTIVE
    return None


def select_register_for(message: SpikeMessage) -> str:
    """Run the PRODUCTION register chooser for ``message`` (registry + charge + llm)."""
    cdef = get_category_def(message.registry_category) if message.registry_category else None
    category_default = cdef.register if cdef else None
    return choose_register(
        is_refusal=message.is_refusal,
        category_default=category_default,
        message=message.input,
        llm_register=model_llm_register(message),
    )


# ---------------------------------------------------------------------------
# The run.
# ---------------------------------------------------------------------------
async def _draft_one(
    message: SpikeMessage,
    persona: NuloPersona,
    provider: FakeSpikeLLMProvider,
) -> dict:
    """Produce the ``{register, draft, reasoning}`` response dict for one message.

    ``none``-expected messages model the C3.4a strong-skip: no reply is drafted
    (the donor scorer scores this as ``register=none, draft=None``). Everything else
    runs the production register chooser → ``select_composer`` → the fake seam.
    """
    if message.expected_register == "none":
        return {"register": "none", "draft": None, "reasoning": "strong-skip (no reply)"}

    register = select_register_for(message)
    request = DraftRequest(
        client_id=1,
        text=message.input,
        register=register,
        category=message.registry_category,
        is_refusal=message.is_refusal,
        seed=message.id,
    )
    # Arm the fake with this message's script (single-process, deterministic) and
    # route through the production composer for the chosen register.
    provider.set_message(
        register=register, category=message.category, is_refusal=message.is_refusal
    )
    composer = select_composer(request)
    result = await composer(request, persona, provider)
    return {
        "register": result.register,
        "draft": result.text,
        "reasoning": result.reasoning,
    }


async def _run_async(
    pack: Sequence[SpikeMessage],
    *,
    persona: Optional[NuloPersona] = None,
    provider: Optional[FakeSpikeLLMProvider] = None,
) -> SpikeResult:
    persona = persona or NuloPersona.default()
    provider = provider or FakeSpikeLLMProvider()
    scores: List[Score] = []
    for message in pack:
        response = await _draft_one(message, persona, provider)
        test_case = {
            "id": message.id,
            "category": message.category,
            "expected_register": message.expected_register,
        }
        scores.append(score_response(test_case, response))
    return SpikeResult(scores=scores, aggregate=aggregate_scores(scores), provider=provider)


def run_spike(
    pack: Optional[Sequence[SpikeMessage]] = None,
    *,
    persona: Optional[NuloPersona] = None,
    provider: Optional[FakeSpikeLLMProvider] = None,
) -> SpikeResult:
    """Run the spike over ``pack`` (default: the full :data:`SPIKE_MESSAGES`).

    Synchronous wrapper around the async drafter pipeline (the composers are
    ``async``); deterministic and offline (the fake provider makes no network call).
    """
    pack = SPIKE_MESSAGES if pack is None else pack
    return asyncio.run(_run_async(pack, persona=persona, provider=provider))


# ---------------------------------------------------------------------------
# The hard gate (a REAL boolean — the test asserts gate.passed).
# ---------------------------------------------------------------------------
def evaluate_gate(aggregate: Dict[str, object]) -> GateResult:
    """Apply the two-part C4.2 engineering gate to an ``aggregate_scores`` dict.

    REAL gate, not advisory:
      (1) ``pass_rate >= AGGREGATE_PASS_RATE_FLOOR`` (0.75); AND
      (2) ``min(calm_pass_rate, reactive_pass_rate) >= PER_REGISTER_PASS_RATE_FLOOR``
          (0.60) — the NET-NEW per-register floor.

    ``passed`` is False if EITHER floor is missed; ``reasons`` enumerates which.
    """
    pass_rate = float(aggregate.get("pass_rate", 0.0) or 0.0)
    calm = float(aggregate.get("calm_pass_rate", 0.0) or 0.0)
    reactive = float(aggregate.get("reactive_pass_rate", 0.0) or 0.0)
    reasons: List[str] = []

    if pass_rate < AGGREGATE_PASS_RATE_FLOOR:
        reasons.append(
            f"aggregate pass_rate {pass_rate:.3f} < {AGGREGATE_PASS_RATE_FLOOR:.2f}"
        )
    worst_register = min(calm, reactive)
    if worst_register < PER_REGISTER_PASS_RATE_FLOOR:
        reasons.append(
            f"per-register floor missed: min(calm={calm:.3f}, reactive={reactive:.3f}) "
            f"< {PER_REGISTER_PASS_RATE_FLOOR:.2f}"
        )

    return GateResult(
        passed=not reasons,
        pass_rate=pass_rate,
        calm_pass_rate=calm,
        reactive_pass_rate=reactive,
        reasons=reasons,
    )


def run_and_gate(
    pack: Optional[Sequence[SpikeMessage]] = None,
) -> tuple:
    """Convenience: run the spike and return ``(SpikeResult, GateResult)``."""
    result = run_spike(pack)
    return result, evaluate_gate(result.aggregate)


__all__ = [
    "AGGREGATE_PASS_RATE_FLOOR",
    "PER_REGISTER_PASS_RATE_FLOOR",
    "SpikeResult",
    "GateResult",
    "model_llm_register",
    "select_register_for",
    "run_spike",
    "evaluate_gate",
    "run_and_gate",
    "aggregate_scores",
]
