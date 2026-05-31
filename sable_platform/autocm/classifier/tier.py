"""LLM tier + category classifier (DESIGN §4 ``classifier/tier``).

SKELETON (full impl = C3.4b). Routes ambiguous-but-engaged traffic through the
LLM to a (tier, category, confidence); injection-hardened per CLASSIFIER §3.
C3.1 fixes the seam shape only — the LLM call itself is C3.4b.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class Classification:
    """The classifier output: autonomy tier + category + confidence."""

    tier: int  # 1 autonomous · 2 HITL-default · 3 escalate
    category: str
    confidence: float
    register: str = "calm"  # 'calm' | 'reactive'


class TierClassifier(Protocol):
    """Classify an engaged message into (tier, category, confidence, register)."""

    async def classify(self, client_id: int, text: str) -> Classification:
        ...


class NotImplementedTierClassifier:
    """Stub classifier — C3.4b replaces it."""

    async def classify(self, client_id: int, text: str) -> Classification:
        raise NotImplementedError("LLM tier/category classifier lands in C3.4b")


__all__ = ["Classification", "TierClassifier", "NotImplementedTierClassifier"]
