"""Register selection — calm vs reactive (DESIGN §4 ``classifier/register``).

SKELETON (full impl = C3.4b). Picks the bimodal-NULO register (calm Bill-Monday
vs reactive HK-47). All hard refusals force the reactive register (SAFETY §0); the
nuanced selection for non-refusal traffic is C3.4b.
"""
from __future__ import annotations

CALM = "calm"
REACTIVE = "reactive"
REGISTERS = (CALM, REACTIVE)


def select_register(text: str, *, is_refusal: bool = False) -> str:
    """Pick the register for a message.

    SKELETON: refusals are always reactive (SAFETY §0, load-bearing now); the
    nuanced non-refusal calm/reactive selection is C3.4b. Defaults to calm.
    """
    if is_refusal:
        return REACTIVE
    return CALM


__all__ = ["CALM", "REACTIVE", "REGISTERS", "select_register"]
