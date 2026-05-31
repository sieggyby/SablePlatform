"""Drafter persona seam (DESIGN §4 ``drafter/persona``).

SKELETON (full impl = C3.3). Holds the bimodal NULO prompt + calibration set and
selects the register-specific composer. The C3.3 build sets prompt caching
(``cache_control: ephemeral``) on the persona system block (claude-api mandate)
and reuses the vendored zero-LLM NULO renderer as the R-4 fallback. C3.1 fixes the
request/result contract + the seam.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol

from sable_platform.autocm.classifier.register import CALM
from sable_platform.autocm.kb.store import KBChunk


@dataclass(frozen=True)
class DraftRequest:
    """Everything the drafter needs to compose one reply."""

    client_id: int
    text: str
    register: str = CALM
    category: Optional[str] = None
    kb_chunks: List[KBChunk] = field(default_factory=list)
    thread_context: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class DraftResult:
    """A composed draft + the chunk_ids it cited (the citation-gate input)."""

    text: str
    register: str
    cited_chunk_ids: List[int] = field(default_factory=list)
    used_llm: bool = True


class Drafter(Protocol):
    """Compose a NULO-voice reply for a :class:`DraftRequest`."""

    async def compose(self, request: DraftRequest) -> DraftResult:
        ...


class NotImplementedDrafter:
    """Stub drafter — C3.3 replaces it."""

    async def compose(self, request: DraftRequest) -> DraftResult:
        raise NotImplementedError("bimodal NULO drafter lands in C3.3")


__all__ = ["DraftRequest", "DraftResult", "Drafter", "NotImplementedDrafter"]
