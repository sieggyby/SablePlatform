"""AutoCM gate (DESIGN §4 ``gate/``).

``safety`` (hard-refusal patterns — D-1 reuse over vendored ``safety``, wired in
C3.1; blocked-path audit + injection flag + demote-on-slip added in C3.5a),
``confidence`` (the C3.5a decision gate: auto vs HITL over ``autocm_category_state``,
freeze + never-auto aware), ``citation_check`` (the three SAFETY §2.5 tiers —
loose / citation-required / exact-match-or-slot-fill, C3.5a), ``autonomy`` (the
DESIGN §7 promotion state machine + auto-demotion triggers, C3.5a),
``review_queue`` (the ``HITLReviewSurface`` seam — TG impl rides C2.7, wired in
C3.1; full review-queue flow C3.5b).
"""
from __future__ import annotations

from .autonomy import (
    AUTO_DEMOTE_RATE,
    HEAVY_EDIT_THRESHOLD,
    MIN_CLEAN_APPROVAL_RATE,
    MIN_SAMPLES,
    DemotionOutcome,
    PromotionVerdict,
    demote_on_safety_slip,
    edit_diff_ratio,
    is_clean_approval,
    is_heavy_edit,
    promote_category,
    promotion_gate,
    sweep_auto_demotions,
)
from .citation_check import (
    TIER_CITATION_REQUIRED,
    TIER_EXACT_MATCH,
    TIER_LOOSE,
    CitationVerdict,
    check_citations,
    check_citations_db,
    tier_for_category,
)
from .confidence import AUTO, HITL, ConfidenceVerdict, decide, is_frozen
from .review_queue import (
    BotSender,
    HITLReviewSurface,
    KBSourceLine,
    ReviewItem,
    ReviewQueueController,
    TelegramReviewSurface,
    WebDashboardReviewSurface,
    build_review_buttons,
    parse_callback_data,
    record_review_decision,
    render_review_message,
)
from .safety import (
    INJECTION_ATTEMPT_FLAG,
    SafetyVerdict,
    audit_injection_blocked,
    audit_safety_block,
    check_safety,
    handle_safety_breach,
)

__all__ = [
    # review_queue
    "HITLReviewSurface",
    "ReviewItem",
    "TelegramReviewSurface",
    "WebDashboardReviewSurface",
    "ReviewQueueController",
    "BotSender",
    "KBSourceLine",
    "build_review_buttons",
    "parse_callback_data",
    "record_review_decision",
    "render_review_message",
    # safety
    "SafetyVerdict",
    "check_safety",
    "audit_safety_block",
    "audit_injection_blocked",
    "handle_safety_breach",
    "INJECTION_ATTEMPT_FLAG",
    # confidence
    "AUTO",
    "HITL",
    "ConfidenceVerdict",
    "decide",
    "is_frozen",
    # citation_check
    "CitationVerdict",
    "check_citations",
    "check_citations_db",
    "tier_for_category",
    "TIER_LOOSE",
    "TIER_CITATION_REQUIRED",
    "TIER_EXACT_MATCH",
    # autonomy
    "PromotionVerdict",
    "DemotionOutcome",
    "promotion_gate",
    "promote_category",
    "sweep_auto_demotions",
    "demote_on_safety_slip",
    "edit_diff_ratio",
    "is_heavy_edit",
    "is_clean_approval",
    "MIN_SAMPLES",
    "MIN_CLEAN_APPROVAL_RATE",
    "HEAVY_EDIT_THRESHOLD",
    "AUTO_DEMOTE_RATE",
]
