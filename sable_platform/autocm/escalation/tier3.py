"""Tier-3 escalation (DESIGN §4 ``escalation/tier3``).

SKELETON. Dual-route tier-3 events (founder + Sable on-call) recorded in
``autocm_escalations``, routed to the operator chat via the C2.7 provisioning
helper. C3.1 fixes the seam shape only.
"""
from __future__ import annotations

from typing import Protocol


class Tier3Escalator(Protocol):
    """Record + dual-route a tier-3 escalation (founder + Sable on-call)."""

    def escalate(self, client_id: int, draft_id: int, reason: str) -> int:
        """Record an ``autocm_escalations`` row; return its id."""
        ...


class NotImplementedTier3Escalator:
    """Stub escalator — wired in a later chunk over C2.7 + SP alerts."""

    def escalate(self, client_id: int, draft_id: int, reason: str) -> int:
        raise NotImplementedError("tier-3 dual-route escalation is wired in a later chunk")


__all__ = ["Tier3Escalator", "NotImplementedTier3Escalator"]
