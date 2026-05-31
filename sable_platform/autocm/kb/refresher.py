"""KB refresher — SP WorkflowRunner-driven freshness contracts + authority/recency.

SKELETON (full impl = C3.2c). Runs as an SP ``WorkflowRunner`` workflow; applies
authority-tiered + recency-weighted retrieval over the C3.2a fused result set and
owns the resolved-FAQ → KB promotion write handler. C3.1 fixes the seam only.
"""
from __future__ import annotations

from typing import Protocol


class KBRefresher(Protocol):
    """Re-extract + re-embed stale sources per their freshness contract."""

    def refresh_client(self, client_id: int) -> int:
        """Refresh all due sources for a client; return the count refreshed."""
        ...


class NotImplementedRefresher:
    """Stub refresher — C3.2c replaces it."""

    def refresh_client(self, client_id: int) -> int:
        raise NotImplementedError("KB refresher lands in C3.2c")


__all__ = ["KBRefresher", "NotImplementedRefresher"]
