"""AutoCM drafter (DESIGN §4 ``drafter/``) — bimodal NULO.

``persona`` (bimodal prompt + calibration, prompt-cached system block),
``compose_calm`` / ``compose_reactive`` (per-register drafters), ``thread_context``
(last N=5). Full impl = C3.3 (prompt caching mandatory). C3.1 ships skeletons.
"""
from __future__ import annotations

from .persona import DraftRequest, DraftResult, Drafter, NotImplementedDrafter

__all__ = ["DraftRequest", "DraftResult", "Drafter", "NotImplementedDrafter"]
