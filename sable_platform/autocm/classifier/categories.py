"""Per-client category registry + thresholds (DESIGN §4 ``classifier/categories``).

SKELETON (full impl = C3.4b). The ~20-entry per-client category registry +
per-category autonomy thresholds, backed by ``autocm_category_state``. C3.1 fixes
the seam shape only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CategoryState:
    """One per-client × per-category autonomy state row (autocm_category_state)."""

    category: str
    state: str  # 'hitl' | 'partial' | 'auto' | 'paused'
    threshold: float
    sample_count: int


class CategoryRegistry(Protocol):
    """Resolve a client's per-category autonomy state + threshold."""

    def get_state(self, client_id: int, category: str) -> CategoryState:
        ...


class NotImplementedCategoryRegistry:
    """Stub registry — C3.4b replaces it."""

    def get_state(self, client_id: int, category: str) -> CategoryState:
        raise NotImplementedError("category registry lands in C3.4b")


__all__ = ["CategoryState", "CategoryRegistry", "NotImplementedCategoryRegistry"]
