"""AutoCM gate (DESIGN §4 ``gate/``).

``safety`` (hard-refusal patterns — D-1 reuse over vendored ``safety``, wired in
C3.1), ``confidence`` (C3.5c), ``citation_check`` (tiered hallucination
prevention, C3.5a), ``review_queue`` (the ``HITLReviewSurface`` seam — TG impl
rides C2.7, wired in C3.1; full review-queue flow C3.5b).
"""
from __future__ import annotations

from .review_queue import (
    HITLReviewSurface,
    ReviewItem,
    TelegramReviewSurface,
    WebDashboardReviewSurface,
)
from .safety import SafetyVerdict, check_safety

__all__ = [
    "HITLReviewSurface",
    "ReviewItem",
    "TelegramReviewSurface",
    "WebDashboardReviewSurface",
    "SafetyVerdict",
    "check_safety",
]
