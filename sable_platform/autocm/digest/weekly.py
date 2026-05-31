"""Weekly digest (DESIGN §4 ``digest/weekly``).

SKELETON (full impl = C3.7). The weekly founder digest (time-saved + community
health + cultist-candidate + subsquad-pollination), scheduled via SP
``WorkflowRunner``. C3.1 fixes the seam shape only.
"""
from __future__ import annotations

from typing import Protocol

# Time-saved formula constants come from the C3.0 per-client baseline table
# (autocm_time_saved_baseline); the formula itself is pinned in C3.7.


class WeeklyDigest(Protocol):
    """Generate + deliver the weekly client digest."""

    def generate(self, client_id: int, week: str) -> str:
        ...


class NotImplementedWeeklyDigest:
    """Stub digest — C3.7 replaces it."""

    def generate(self, client_id: int, week: str) -> str:
        raise NotImplementedError("weekly digest lands in C3.7")


__all__ = ["WeeklyDigest", "NotImplementedWeeklyDigest"]
