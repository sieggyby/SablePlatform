"""AutoCM classifier (DESIGN §4 ``classifier/``).

``filter`` (heuristic-first engagement gate — D-1 reuse over vendored
``engagement``, wired in C3.1; the stateful strong-skips are added C3.4a),
``tier`` (LLM tier+category, C3.4b), ``register`` (calm vs reactive, C3.4b),
``categories`` (per-client registry + thresholds, C3.4b).
"""
from __future__ import annotations

from .filter import EngagementResult, FilterDecision, assess_engagement

__all__ = ["EngagementResult", "FilterDecision", "assess_engagement"]
