"""Register dispatch (C3.3) — the bimodal drafter that routes to the right composer.

The MEGAPLAN C3.3 exit names "register selection feeds the right composer": the
classifier (C3.4b) chooses calm | reactive; :class:`BimodalDrafter` routes a
:class:`~sable_platform.autocm.drafter.persona.DraftRequest` to
:func:`~sable_platform.autocm.drafter.compose_calm.compose_calm` or
:func:`~sable_platform.autocm.drafter.compose_reactive.compose_reactive` accordingly.
A hard refusal forces reactive regardless of the requested register (SAFETY §0 —
refusals are charged content by definition, never overridden), so an upstream bug
that left ``register=calm`` on a refusal still routes to the refusal composer.

The drafter is constructed with a :class:`~sable_platform.autocm.drafter.persona.NuloPersona`
(the bimodal prompt + calibration set) and an injected
:class:`~sable_platform.autocm.llm.LLMProvider` (the C3.1 seam). It satisfies the
:class:`~sable_platform.autocm.drafter.persona.Drafter` protocol.
"""
from __future__ import annotations

from sable_platform.autocm.classifier.register import CALM, REACTIVE
from sable_platform.autocm.drafter.compose_calm import compose_calm
from sable_platform.autocm.drafter.compose_reactive import compose_reactive
from sable_platform.autocm.drafter.persona import (
    DraftRequest,
    DraftResult,
    NuloPersona,
)
from sable_platform.autocm.llm import LLMProvider


def select_composer(request: DraftRequest):
    """Return the composer callable for ``request``'s register (refusal → reactive).

    A hard refusal ALWAYS selects the reactive composer (SAFETY §0). Otherwise the
    classifier-chosen ``register`` selects: ``reactive`` → reactive composer, anything
    else (the calm floor) → calm composer. Pure — no I/O — so the routing decision is
    independently assertable.
    """
    if request.is_refusal or request.register == REACTIVE:
        return compose_reactive
    return compose_calm


class BimodalDrafter:
    """The C3.3 bimodal NULO drafter — routes by register to the right composer."""

    def __init__(self, persona: NuloPersona, provider: LLMProvider) -> None:
        self._persona = persona
        self._provider = provider

    async def compose(self, request: DraftRequest) -> DraftResult:
        """Route to the register-appropriate composer and compose one reply.

        Never raises (the composers honor the seam contract); a provider that returns
        ``None`` yields a deterministic in-voice fallback draft.
        """
        composer = select_composer(request)
        return await composer(request, self._persona, self._provider)


__all__ = ["select_composer", "BimodalDrafter", "CALM", "REACTIVE"]
