"""AutoCM knowledge base (DESIGN §4 ``kb/``).

The KB family: ``store`` (chunk+embed+index, C3.2a), ``extractor`` /
``onchain`` (source adapters, C3.2b), ``refresher`` (freshness contracts,
C3.2c), and ``constants`` — the slot-fill registry that bridges the vendored
``sable_pulse_core.slotfill`` engine (D-1 reuse, wired in C3.1).
"""
from __future__ import annotations

from .constants import ConstantsKB, build_slotfill_kb
from .extractor import FakeHttpFetcher, HttpxFetcher, KBExtractor
from .onchain import (
    AlchemyOnchainAdapter,
    FakeRpcTransport,
    HttpRpcTransport,
    OnchainAdapterRegistry,
)
from .refresher import (
    FreshnessVerdict,
    KBRefresher,
    PromotionResult,
    RankedChunk,
    check_cited_freshness,
    freshness_contract,
    is_source_due,
    promote_resolved_faq,
    rank_chunks,
    search_and_rank,
    utc_now,
)

__all__ = [
    "ConstantsKB",
    "build_slotfill_kb",
    # extractor (C3.2b)
    "KBExtractor",
    "HttpxFetcher",
    "FakeHttpFetcher",
    # onchain (C3.2b)
    "OnchainAdapterRegistry",
    "AlchemyOnchainAdapter",
    "HttpRpcTransport",
    "FakeRpcTransport",
    # refresher (C3.2c)
    "KBRefresher",
    "freshness_contract",
    "is_source_due",
    "rank_chunks",
    "RankedChunk",
    "search_and_rank",
    "check_cited_freshness",
    "FreshnessVerdict",
    "promote_resolved_faq",
    "PromotionResult",
    "utc_now",
]
