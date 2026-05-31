"""Citation / hallucination gate (DESIGN §4 ``gate/citation_check``).

SKELETON (full impl = C3.5a). Tiered hallucination prevention: a draft may cite
ONLY retrieval-surfaced chunks; a citation-required category with no authoritative
chunk auto-rejects to HITL. C3.1 fixes the seam shape only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class CitationVerdict:
    passed: bool
    reason: str
    cited_chunk_ids: List[int]


def check_citations(
    draft_text: str, cited_chunk_ids: List[int], available_chunk_ids: List[int], *, required: bool
) -> CitationVerdict:
    """Verify citations are retrieval-grounded. SKELETON — C3.5a implements."""
    raise NotImplementedError("citation/hallucination gate lands in C3.5a")


__all__ = ["CitationVerdict", "check_citations"]
