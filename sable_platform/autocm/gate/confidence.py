"""Confidence + autonomy gate (DESIGN §4 ``gate/confidence``).

SKELETON (full impl = C3.5c). Compares a draft's confidence against the
per-category threshold + autonomy state to decide AUTO vs HITL. C3.1 fixes the
seam shape only.
"""
from __future__ import annotations

from dataclasses import dataclass

# Gate outcomes.
AUTO = "auto"
HITL = "hitl"


@dataclass(frozen=True)
class ConfidenceVerdict:
    outcome: str  # 'auto' | 'hitl'
    reason: str


def decide(confidence: float, threshold: float, autonomy_state: str) -> ConfidenceVerdict:
    """Decide AUTO vs HITL. SKELETON — C3.5c implements the full state machine."""
    raise NotImplementedError("confidence/autonomy gate lands in C3.5c")


__all__ = ["AUTO", "HITL", "ConfidenceVerdict", "decide"]
