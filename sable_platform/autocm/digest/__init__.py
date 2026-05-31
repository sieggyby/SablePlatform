"""AutoCM digest (DESIGN §4 ``digest/``).

``weekly`` (A+C headline = time-saved + community-health, C3.7), ``analytics``
(sentiment, FAQ frequency, ratios, voice-drift, C3.7). Skeletons.
"""
from __future__ import annotations

from .weekly import NotImplementedWeeklyDigest, WeeklyDigest

__all__ = ["WeeklyDigest", "NotImplementedWeeklyDigest"]
