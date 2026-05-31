"""Reactive-register drafter (DESIGN §4 ``drafter/compose_reactive``).

SKELETON (full impl = C3.3). The reactive HK-47 register composer (all hard
refusals route here). C3.1 fixes the seam; the prompt-cached compose lands in C3.3.
"""
from __future__ import annotations

from sable_platform.autocm.drafter.persona import DraftRequest, DraftResult


async def compose_reactive(request: DraftRequest) -> DraftResult:
    """Compose a reactive-register reply. SKELETON — C3.3 implements."""
    raise NotImplementedError("reactive-register drafter lands in C3.3")


__all__ = ["compose_reactive"]
