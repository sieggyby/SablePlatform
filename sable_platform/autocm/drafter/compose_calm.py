"""Calm-register drafter (DESIGN §4 ``drafter/compose_calm``).

SKELETON (full impl = C3.3). The calm Bill-Monday register composer. C3.1 fixes
the seam; the prompt-cached compose lands in C3.3.
"""
from __future__ import annotations

from sable_platform.autocm.drafter.persona import DraftRequest, DraftResult


async def compose_calm(request: DraftRequest) -> DraftResult:
    """Compose a calm-register reply. SKELETON — C3.3 implements."""
    raise NotImplementedError("calm-register drafter lands in C3.3")


__all__ = ["compose_calm"]
