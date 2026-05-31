"""AutoCM escalation (DESIGN §4 ``escalation/``).

``tier3`` — dual-route tier-3 escalation (founder + Sable on-call). Skeleton;
full impl rides the C2.7 operator-chat provisioning + SP alert wiring.
"""
from __future__ import annotations

from .tier3 import NotImplementedTier3Escalator, Tier3Escalator

__all__ = ["Tier3Escalator", "NotImplementedTier3Escalator"]
