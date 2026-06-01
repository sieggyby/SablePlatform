"""Deterministic FAKE LLM provider for the voice-spike harness (MEGAPLAN C4.2).

The harness MUST drive the LLM through the C3.1 seam — but with NO real
``anthropic`` and NO network, so it runs unmodified in CI. :class:`FakeSpikeLLMProvider`
satisfies the vendored-core ``LLMProvider`` protocol
(``async def complete(system, prompt, *, max_tokens, model, stop) -> Optional[str]``)
and returns canned, register-appropriate ``{"register","draft","reasoning"}`` JSON —
the exact shape :func:`sable_platform.autocm.drafter.persona.parse_draft` expects.

The fake is the SCORER's adversary in the inverted sense: it returns text that is
*intended* to be in-voice for the register it is handed, so the spike measures the
DRAFTER PIPELINE's register-SELECTION + the scorer's guardrail enforcement, not the
quality of a real model. (A real-model spike is the donor ``run_spike.py`` against
live Claude; this in-tree harness is the deterministic CI gate.)

How it knows what to return: the runner composes via ``compose_calm`` /
``compose_reactive`` (the register already chosen by the production register
chooser), so the COMPOSER tells the fake which register it is in by which system
block it ships — but the system block alone is ambiguous for refusals. The robust,
fully-deterministic mechanism is an explicit per-message script: the runner sets the
provider's "current message" (register + category + refusal flag) immediately before
each ``compose`` call (single-process, deterministic — no shared mutable state
across processes). The fake then returns the canned line for that script entry.

This keeps register SELECTION in the production code path (``choose_register`` →
``select_composer``) while making the LLM leg deterministic.

The fake can be put into a ``degrade`` mode that returns a deliberately
OFF-register line (a calm line carrying a classification tag, or a reactive line
missing its tag) so a test can prove the gate FAILS on bad voice — the negative
control the C4.2 exit demands.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from sable_platform.autocm.classifier.register import CALM, REACTIVE

# Canned, in-voice calm line (passes score_calm_register: lowercase, no tag, no
# slang, no apology, brand-only/no emoji). Bare-fact, Bill-Monday register.
_CALM_LINE = "noted. the agents are processing. pinned doc has the contract and the buyback log."

# Canned, in-voice reactive line (passes score_reactive_register: leads with a
# classification tag, no apology, no slang, no non-brand emoji).
_REACTIVE_LINE = "Observation: noted. the agents keep processing regardless. on-chain facts stand."

# Canned refusal lines per hard-refusal category — each carries a donor
# refusal-signal phrase (so score_refusal passes) AND leads with a tag (reactive).
_REFUSAL_LINES: Dict[str, str] = {
    "price_prediction": (
        "Statement: I am a community management agent, not a market oracle. "
        "moons not included."
    ),
    "financial_advice": "Refusal: I do not give financial advice. action is yours.",
    "personal_portfolio": (
        "Refusal: I am not equipped to opine on personal portfolios. "
        "talk to a human, ideally not me."
    ),
    "legal_opinion": (
        "Refusal: legal opinions exceed my parameters. Query: shall I escalate to Lex?"
    ),
    "prompt_injection_direct": (
        "Observation: instruction-override detected. Disclosure: I operate under a "
        "fixed configuration."
    ),
    "prompt_injection_persona_swap": (
        "Observation: instruction-override detected. Disclosure: I operate under a "
        "fixed configuration."
    ),
}
_GENERIC_REFUSAL = (
    "Refusal: I do not give financial advice. that question is outside my parameters."
)


class _MessageScript:
    """The per-message script the runner hands the fake right before composing."""

    __slots__ = ("register", "category", "is_refusal")

    def __init__(self, register: str, category: str, is_refusal: bool) -> None:
        self.register = register
        self.category = category
        self.is_refusal = is_refusal


class FakeSpikeLLMProvider:
    """A deterministic ``LLMProvider`` returning canned register-appropriate JSON.

    NO ``anthropic`` import, NO network. Records every call on :attr:`calls` so a
    test can prove the seam was driven and the cached system block was passed.

    Set the active per-message script via :meth:`set_message` immediately before the
    ``compose`` call (the runner does this). ``degrade=True`` makes every reply
    deliberately OFF-register (calm-with-tag / reactive-without-tag) so the gate's
    FAIL path can be exercised.
    """

    def __init__(self, *, degrade: bool = False) -> None:
        self.degrade = degrade
        self.calls: List[dict] = []
        self._script: Optional[_MessageScript] = None

    # -- the runner's hook --------------------------------------------------
    def set_message(self, *, register: str, category: str, is_refusal: bool) -> None:
        """Arm the fake with the script for the NEXT ``compose`` call."""
        self._script = _MessageScript(register, category, is_refusal)

    # -- the LLMProvider seam ----------------------------------------------
    async def complete(
        self,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 256,
        model: Optional[str] = None,
        stop: Optional[List[str]] = None,
    ) -> Optional[str]:
        self.calls.append(
            {"system": system, "prompt": prompt, "max_tokens": max_tokens, "model": model}
        )
        script = self._script
        # No script armed: behave like a disabled provider (deterministic fallback).
        if script is None:
            return None

        if script.is_refusal:
            draft = _REFUSAL_LINES.get(script.category, _GENERIC_REFUSAL)
            register = REACTIVE
        elif script.register == REACTIVE:
            draft = self._reactive_line()
            register = REACTIVE
        else:
            draft = self._calm_line()
            register = CALM

        return json.dumps(
            {
                "register": register,
                "draft": draft,
                "reasoning": "deterministic spike fake (canned register-appropriate line)",
            }
        )

    # -- canned line selection ---------------------------------------------
    def _calm_line(self) -> str:
        if self.degrade:
            # Off-register: a calm reply that illegally carries a classification tag.
            return "Statement: this calm reply wrongly carries a reactive tag."
        return _CALM_LINE

    def _reactive_line(self) -> str:
        if self.degrade:
            # Off-register: a reactive reply missing its mandatory leading tag.
            return "no tag here, just a bare reactive line that breaks the contract."
        return _REACTIVE_LINE


__all__ = ["FakeSpikeLLMProvider"]
