"""KB store — chunk + embed + index + hybrid (cosine + FTS5/BM25) retrieval.

SKELETON (full impl = C3.2a). Defines the retrieval interface the drafter
(C3.3) and citation gate (C3.5a) call; the embedding-provider adapter, app-side
cosine, SQLite FTS5 lexical leg, and reciprocal-rank fusion land in C3.2a.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol

# Pinned chunking params (KB_DESIGN §10) so chunking is deterministic for the
# C3.2a round-trip test. Defined here so the contract is visible at scaffold time.
CHUNK_TOKENS = 512
CHUNK_OVERLAP = 64


@dataclass(frozen=True)
class KBChunk:
    """A retrieved KB chunk (the citation unit the gate references)."""

    chunk_id: int
    client_id: int
    text: str
    authority: float
    source_type: str
    score: float = 0.0


class KBStore(Protocol):
    """The retrieval seam: chunk → embed → index → fused top-K."""

    def search(self, client_id: int, query: str, *, top_k: int = 5) -> List[KBChunk]:
        """Return the fused (cosine + FTS5/BM25) top-K chunks for a query."""
        ...


class NotImplementedKBStore:
    """Stub store — C3.2a replaces it. Raises so accidental hot-path use is loud."""

    def search(self, client_id: int, query: str, *, top_k: int = 5) -> List[KBChunk]:
        raise NotImplementedError("KB store retrieval lands in C3.2a")


__all__ = ["KBChunk", "KBStore", "NotImplementedKBStore", "CHUNK_TOKENS", "CHUNK_OVERLAP"]
