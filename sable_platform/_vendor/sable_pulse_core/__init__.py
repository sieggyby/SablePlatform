"""`sable_pulse.core` — the FROZEN, vendored deterministic engine.

This package is the single donor of the reusable, dependency-light engine that is
ONE-WAY synced into `sable_platform/_vendor/sable_pulse_core/` (it is NOT a
pip-installable package — see API.md + scripts/sync_vendor.py). Everything exported
here is part of the documented public API that downstream consumers (SableAutoCM,
C3.1) import against; the API is semantic-versioned via `CORE_API_VERSION`.

PURITY CONTRACT (asserted by tests/test_core_purity.py):
  * heavy deps: pyyaml + httpx ONLY. NO telegram, NO anthropic, NO sable_platform
    import anywhere under `core` (at module top OR transitively).
  * every `core` module begins with `from __future__ import annotations`.
  * no value-position 3.10+ syntax (match/case, PEP-604 value unions, ExceptionGroup)
    so `import sable_pulse.core` runs clean on Python 3.9.
  * the persona/NULO YAML banks ship as PACKAGE DATA under `core/personas/` and the
    loader resolves them PACKAGE-relative (never REPO_ROOT), so they travel with the
    vendored copy.

The LLM is a protocol-only seam (`LLMProvider`) — `core` never ships an LLM client.
"""
from __future__ import annotations

# Semantic version of the FROZEN public API below. Bump on any breaking change to
# an exported symbol; the vendor snapshot records this alongside the content hash.
CORE_API_VERSION = "1.0.0"

# ---- cache ------------------------------------------------------------------
from .cache import CacheEntry, JsonCache

# ---- engagement (AutoCM vendors this) ---------------------------------------
from .engagement import EngagementResult, assess

# ---- llm seam (protocol only; zero anthropic) -------------------------------
from .llm import LLMProvider

# ---- nulo (deterministic zero-LLM persona renderer; AutoCM R-4 fallback) ----
from .nulo import (
    CALM as NULO_CALM,
    REACTIVE as NULO_REACTIVE,
    REGISTERS as NULO_REGISTERS,
    Register,
    load_nulo,
    render as render_nulo,
)

# ---- rate limiting ----------------------------------------------------------
from .ratelimit import RateLimitConfig, RateLimitDecision, RateLimiter

# ---- router (deterministic category classifier) -----------------------------
from .router import CATEGORIES as ROUTER_CATEGORIES
from .router import classify

# ---- safety (AutoCM vendors this) -------------------------------------------
from .safety import (
    CATEGORIES as SAFETY_CATEGORIES,
    CONTENT_BLOCK_CATEGORIES,
    HARD_REFUSAL_CATEGORIES,
    RefusalMatch,
    check_refusal,
)

# ---- slotfill (AutoCM vendors this) -----------------------------------------
from .slotfill import SlotFillKB

# ---- templates / persona engine + PACKAGE-relative persona loader -----------
from .templates import (
    PERSONAS_ROOT,
    Persona,
    build_review_card,
    composite_and_regime,
    derive_conditions,
    humanize_age,
    humanize_pct,
    humanize_usd,
    load_personas,
    persona_data_dir,
    pick_template,
    render_persona_line,
)

# ---- source seams + data contracts ------------------------------------------
from .sources.base import (
    CommitteeCall,
    CommitteeSource,
    DevActivity,
    GitHubSource,
    MarketData,
    MarketSource,
)
from .sources.committee import PublicPageCommitteeSource
from .sources.github import GitHubRepoSource
from .sources.market import DexscreenerSource

__all__ = [
    "CORE_API_VERSION",
    # cache
    "JsonCache",
    "CacheEntry",
    # engagement
    "assess",
    "EngagementResult",
    # llm seam
    "LLMProvider",
    # ratelimit
    "RateLimiter",
    "RateLimitConfig",
    "RateLimitDecision",
    # router
    "classify",
    "ROUTER_CATEGORIES",
    # safety
    "check_refusal",
    "RefusalMatch",
    "SAFETY_CATEGORIES",
    "HARD_REFUSAL_CATEGORIES",
    "CONTENT_BLOCK_CATEGORIES",
    # slotfill
    "SlotFillKB",
    # nulo (deterministic zero-LLM persona renderer)
    "load_nulo",
    "render_nulo",
    "Register",
    "NULO_CALM",
    "NULO_REACTIVE",
    "NULO_REGISTERS",
    # templates / persona engine
    "Persona",
    "load_personas",
    "persona_data_dir",
    "PERSONAS_ROOT",
    "build_review_card",
    "render_persona_line",
    "pick_template",
    "derive_conditions",
    "composite_and_regime",
    "humanize_usd",
    "humanize_pct",
    "humanize_age",
    # sources
    "MarketData",
    "CommitteeCall",
    "DevActivity",
    "MarketSource",
    "CommitteeSource",
    "GitHubSource",
    "DexscreenerSource",
    "PublicPageCommitteeSource",
    "GitHubRepoSource",
]
