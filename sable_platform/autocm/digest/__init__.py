"""AutoCM digest (MEGAPLAN C3.7 — DESIGN §4 ``digest/`` / DIGEST.md).

``weekly`` (the A+C headline = time-saved + community-health, the cultist /
subsquad / FAQ / sentiment / voice-drift sections, the founder button set,
preview-vs-deliver routing, the no-deliver alarm) + ``analytics`` (sentiment via
the LLM seam, FAQ-frequency clustering, auto/HITL/clean ratios reusing C3.5a
``gather_review_stats``, voice-drift detection, cultist-candidate + topic-cluster
member analytics over the C1.1 ``relay_messages`` corpus).
"""
from __future__ import annotations

from sable_platform.autocm.digest import analytics, weekly
from sable_platform.autocm.digest.weekly import (
    DigestDelivery,
    WeeklyDigestReport,
    deliver,
    generate,
    raise_no_deliver_alarm,
    record_interaction,
)

__all__ = [
    "analytics",
    "weekly",
    "DigestDelivery",
    "WeeklyDigestReport",
    "deliver",
    "generate",
    "raise_no_deliver_alarm",
    "record_interaction",
]
