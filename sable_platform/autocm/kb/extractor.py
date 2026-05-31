"""KB source extractor — web/RSS/doc → chunks.

SKELETON (full impl = C3.2b). Defines the extractor seam; web/RSS/doc parsing +
chunking at the pinned KB_DESIGN params land in C3.2b.
"""
from __future__ import annotations

from typing import List, Protocol


class SourceExtractor(Protocol):
    """Turn a configured source (website/RSS/substack/doc) into raw text chunks."""

    def extract(self, source_config: dict) -> List[str]:
        """Return raw text chunks for a single ``autocm_kb_sources`` row."""
        ...


class NotImplementedExtractor:
    """Stub extractor — C3.2b replaces it."""

    def extract(self, source_config: dict) -> List[str]:
        raise NotImplementedError("KB extractor lands in C3.2b")


__all__ = ["SourceExtractor", "NotImplementedExtractor"]
