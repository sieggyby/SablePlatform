"""AutoCM drafter (DESIGN §4 ``drafter/``) — bimodal NULO (C3.3).

``persona`` (bimodal prompt + calibration set, prompt-cached system block + the
``catchphrase_repetition`` mantra state), ``compose_calm`` / ``compose_reactive``
(per-register drafters over the C3.1 ``LLMProvider`` seam), ``dispatch``
(register → composer routing), ``thread_context`` (last N=5). The real adapter
sets ``cache_control: ephemeral`` on the cached system block (prompt caching
mandatory); tests use a deterministic FAKE provider (no real Anthropic / network).

Voice QUALITY is trust-gated by the C4.2 voice spike + Lex sign-off (not a build
blocker); C3.3 builds the structure and asserts the objective ``scorer.py``
predicate subset.
"""
from __future__ import annotations

from .compose_calm import compose_calm
from .compose_reactive import compose_reactive
from .dispatch import BimodalDrafter, select_composer
from .persona import (
    DRAFT_MAX_TOKENS,
    Drafter,
    DraftRequest,
    DraftResult,
    MantraState,
    NuloPersona,
    build_cached_request,
    parse_draft,
)
from .thread_context import (
    THREAD_CONTEXT_N,
    load_thread_context,
    truncate_thread_context,
)

__all__ = [
    # persona / prompt-cache
    "NuloPersona",
    "MantraState",
    "DraftRequest",
    "DraftResult",
    "Drafter",
    "DRAFT_MAX_TOKENS",
    "build_cached_request",
    "parse_draft",
    # composers + dispatch
    "compose_calm",
    "compose_reactive",
    "select_composer",
    "BimodalDrafter",
    # thread context
    "THREAD_CONTEXT_N",
    "load_thread_context",
    "truncate_thread_context",
]
