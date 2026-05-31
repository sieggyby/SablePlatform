"""Digest analytics (DESIGN §4 ``digest/analytics``).

SKELETON (full impl = C3.7). The bot-internal member-analytics signals computed
over the C1.1 ``relay_messages`` corpus: ``score_sentiment``,
``frequent_questions``, ``cultist_candidates``, ``topic_clusters`` (subsquad
pollination), voice-drift ratios. C3.1 fixes the seam shape only.
"""
from __future__ import annotations

from typing import List, Protocol


class DigestAnalytics(Protocol):
    """The simple v1 signals (AutoCM does NOT consume SP's richer analytics in v1)."""

    def score_sentiment(self, client_id: int, week: str) -> float:
        ...

    def frequent_questions(self, client_id: int, week: str) -> List[tuple[str, int]]:
        ...


class NotImplementedDigestAnalytics:
    """Stub analytics — C3.7 replaces it."""

    def score_sentiment(self, client_id: int, week: str) -> float:
        raise NotImplementedError("digest analytics land in C3.7")

    def frequent_questions(self, client_id: int, week: str) -> List[tuple[str, int]]:
        raise NotImplementedError("digest analytics land in C3.7")


__all__ = ["DigestAnalytics", "NotImplementedDigestAnalytics"]
