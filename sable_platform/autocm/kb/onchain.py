"""KB on-chain source — per-client RPC adapter (Alchemy free tier).

SKELETON (full impl = C3.2b). The per-client RPC key MUST come from the client's
own config (no key bleed across clients — the C3.2b security property); C3.1 fixes
the seam shape only.
"""
from __future__ import annotations

from typing import List, Protocol


class OnchainAdapter(Protocol):
    """Per-client chain RPC adapter — each client's calls use only that client's key."""

    def query(self, client_id: int, query_name: str) -> dict:
        """Run a named on-chain query (e.g. ``vault_tvl``) for one client."""
        ...

    def supported_queries(self) -> List[str]:
        ...


class NotImplementedOnchainAdapter:
    """Stub adapter — C3.2b replaces it."""

    def query(self, client_id: int, query_name: str) -> dict:
        raise NotImplementedError("on-chain adapter lands in C3.2b")

    def supported_queries(self) -> List[str]:
        return []


__all__ = ["OnchainAdapter", "NotImplementedOnchainAdapter"]
