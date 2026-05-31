"""AutoCM escalation (DESIGN §4 ``escalation/``).

``tier3`` — the C3.8a tier-3 dual-route (founder + Sable on-call / Arf),
conflict/moderation Arf-only routing + auto-silence, the DESIGN §7 trigger-4
founder-complaint demote, and the SAFETY §6 client-wide 48h pure-HITL freeze.
"""
from __future__ import annotations

from .tier3 import (
    ARF_ONLY_CATEGORIES,
    DUAL_ROUTE_CATEGORIES,
    EscalationNotifier,
    EscalationResult,
    FrozenCategory,
    RoutePlan,
    ROUTE_ARF_ONLY,
    ROUTE_DUAL,
    Tier3Escalator,
    Tier3EscalationRouter,
    auto_silence_user,
    demote_on_founder_complaint,
    freeze_client,
    restore_expired_freezes,
    route_for_category,
)

__all__ = [
    "Tier3Escalator",
    "Tier3EscalationRouter",
    "EscalationNotifier",
    "EscalationResult",
    "RoutePlan",
    "route_for_category",
    "ROUTE_DUAL",
    "ROUTE_ARF_ONLY",
    "DUAL_ROUTE_CATEGORIES",
    "ARF_ONLY_CATEGORIES",
    "auto_silence_user",
    "demote_on_founder_complaint",
    "FrozenCategory",
    "freeze_client",
    "restore_expired_freezes",
]
