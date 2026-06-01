"""SableAutoCM — the bimodal-NULO LLM community manager (MEGAPLAN C3.x).

``sable_platform.autocm`` is the AutoCM product layer built ON the SableRelay
substrate (``sable_platform.relay``) and SablePlatform's foundational layer
(audit / cost / member identity / workflow engine / alerts). It reuses
sable-pulse's deterministic engine via the in-tree vendored copy
``sable_platform._vendor.sable_pulse_core`` — never the sibling ``sable_pulse``
repo (SP pillar-1; see CLAUDE.md "Architecture Decisions" for the vendoring
deviation record).

This is the **C3.1 scaffolding**: the DESIGN §4 package layout, the
``ClientConfig`` / ``PersonaSpec`` loaders, the three productization seams
(``HITLReviewSurface`` / ``LLMProvider`` adapter / deployment manifest), and the
D-1 reuse wiring. Full implementations land in C3.2 → C3.10.

The D-1 reuse is wired here NOW (not stubbed):

  * ``autocm.classifier.filter``  ← vendored ``engagement.assess``
  * ``autocm.gate.safety``        ← vendored ``safety.check_refusal``
  * ``autocm.kb.constants``       ← vendored ``slotfill.SlotFillKB``

Importing this package pulls in only the lightweight loaders + seam interfaces;
the heavy per-pipeline modules are imported lazily by their owners (C3.2+).
"""
from __future__ import annotations

# --- loaders (C3.1 §5) -------------------------------------------------------
from .loaders import ClientConfig, PersonaSpec, load_client_config, load_persona_spec

# --- deployment manifest schema (seam 3, C3.1 §6) ----------------------------
from .manifest import (
    DeploymentManifest,
    ManifestSecretError,
    load_manifest,
)

# --- the three productization seams ------------------------------------------
from .gate.review_queue import (
    HITLReviewSurface,
    ReviewItem,
    ReviewQueueController,
    TelegramReviewSurface,
    WebDashboardReviewSurface,
)
from .llm import (
    AnthropicProvider,
    LLMProvider,
    NullLLMProvider,
    build_llm_provider,
)

# --- per-client cost accounting (C3.10; in-process, migration-free) ----------
from .cost import CostAccountant, price_for_usage

__all__ = [
    # loaders
    "ClientConfig",
    "PersonaSpec",
    "load_client_config",
    "load_persona_spec",
    # manifest seam
    "DeploymentManifest",
    "ManifestSecretError",
    "load_manifest",
    # HITL review-surface seam
    "HITLReviewSurface",
    "ReviewItem",
    "ReviewQueueController",
    "TelegramReviewSurface",
    "WebDashboardReviewSurface",
    # LLM provider seam
    "LLMProvider",
    "AnthropicProvider",
    "NullLLMProvider",
    "build_llm_provider",
    # cost accounting (C3.10)
    "CostAccountant",
    "price_for_usage",
]
