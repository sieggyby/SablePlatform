"""Per-client category registry + per-category confidence thresholds.

(MEGAPLAN C3.4b — the tri-tier ~20-entry category registry, DESIGN §5 /
CLASSIFIER §2 table. This is the FULL CLASSIFIER §2 set, not only the tier-2/3
additions: every tier-1 autonomous category, the hard-refusal tier-1 set, the
tier-2 set, and the FULL tier-3 set.)

The registry is the static, code-owned source of truth for, per category:

  * ``tier``               — the autonomy tier (1 autonomous · 2 HITL-default ·
                             3 escalate-only). CLASSIFIER §2 "tier default".
  * ``register``           — the bimodal-NULO default register (calm | reactive |
                             None when the category never auto-replies). VOICE.md /
                             CLASSIFIER §2.5.
  * ``confidence_floor``   — the per-category confidence threshold (CLASSIFIER §2:
                             default floor 0.85, higher for higher-risk, lower for
                             low-risk). A tier-classify below this floor is NOT
                             eligible for autonomous send even if the category is in
                             the ``auto`` state.
  * ``auto_eligible``      — whether the category may EVER auto-send. Tier-3 and the
                             never-auto tier-2 routing categories (conflict_detected,
                             moderation_flag, incident) are NEVER autonomous —
                             ``auto_eligible`` is False and ``confidence_floor`` is
                             None (N/A in the §2 table).
  * ``hard_refusal``       — the tier-1 hard-refusal categories (price_prediction /
                             financial_advice / legal): always reactive register,
                             never overridden (SAFETY §1 / VOICE §4).

The ``autocm_category_state`` table (058) holds the per-CLIENT × per-CATEGORY
RUNTIME state (``state`` ∈ {hitl, auto}, the tunable ``confidence_threshold``,
``sample_count`` for the autonomy state machine, and the SAFETY §6 freeze
columns). The registry here is the per-client-INDEPENDENT default surface the
C3.5a gate reads ALONGSIDE the runtime row; :func:`resolve_category_state`
overlays a runtime row onto the registry default (runtime threshold wins when
present), so the gate sees one merged view.

This module imports no telegram / anthropic; the runtime-row read is via the
named ``sable_platform.autocm.db``-style helper (SQL behind a function), and the
registry itself is pure data — deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.autocm.classifier.register import CALM, REACTIVE

# ---------------------------------------------------------------------------
# Tier constants (CLASSIFIER §2 tier definitions)
# ---------------------------------------------------------------------------
TIER_AUTONOMOUS = 1  # handleable from KB + drafter; safe to auto-post if `auto`
TIER_HITL = 2  # drafter composes, a human reviews
TIER_ESCALATE = 3  # needs founder; bot escalates, does not draft a substantive answer

#: the runtime autonomy states the 058 CHECK enforces on autocm_category_state.state
RUNTIME_STATES = ("hitl", "auto")

#: CLASSIFIER §2 default confidence floor (the floor for any category not given a
#: higher/lower per-category override below).
DEFAULT_CONFIDENCE_FLOOR = 0.85


@dataclass(frozen=True)
class CategoryDef:
    """One immutable registry entry — the per-category routing defaults.

    ``confidence_floor`` is ``None`` for the never-auto categories (the §2 table's
    "N/A" rows: tier-3 + conflict_detected/moderation_flag/incident) — there is no
    confidence at which they auto-send, so a floor is meaningless. ``register`` is
    ``None`` for the categories that never produce a public reply at all (tier-3
    no-auto-reply + conflict_detected/moderation_flag) — the §2 "register n/a" rows.
    """

    category: str
    tier: int
    register: Optional[str]  # 'calm' | 'reactive' | None (no public reply)
    confidence_floor: Optional[float]  # None ⇔ never auto (N/A in §2)
    auto_eligible: bool
    hard_refusal: bool = False
    #: human-readable note mirroring the CLASSIFIER §2 "Notes" column (audit/docs).
    note: str = ""


def _t1(
    category: str,
    register: str,
    floor: float = DEFAULT_CONFIDENCE_FLOOR,
    *,
    hard_refusal: bool = False,
    note: str = "",
) -> CategoryDef:
    """Build a tier-1 autonomous-eligible category (auto_eligible=True)."""
    return CategoryDef(
        category=category,
        tier=TIER_AUTONOMOUS,
        register=register,
        confidence_floor=floor,
        auto_eligible=True,
        hard_refusal=hard_refusal,
        note=note,
    )


def _t2_review(
    category: str, register: str, floor: float = DEFAULT_CONFIDENCE_FLOOR, *, note: str = ""
) -> CategoryDef:
    """Build a tier-2 HITL category that DOES draft (auto-eligible after promotion).

    Tier-2 categories default to ``hitl`` in ``autocm_category_state`` but ARE
    promotable to ``auto`` via the C3.5a autonomy state machine — so they are
    ``auto_eligible`` and carry a confidence floor.
    """
    return CategoryDef(
        category=category,
        tier=TIER_HITL,
        register=register,
        confidence_floor=floor,
        auto_eligible=True,
        note=note,
    )


def _never_auto(
    category: str, tier: int, register: Optional[str], *, note: str = ""
) -> CategoryDef:
    """Build a NEVER-auto category (§2 "N/A" threshold rows).

    Covers the tier-3 set (threat / whale_inbound / founder_voice_needed / incident)
    AND the tier-2 operator-only routing categories (conflict_detected /
    moderation_flag) that route to Arf and never auto-reply. ``confidence_floor`` is
    ``None`` (no confidence makes them auto) and ``auto_eligible`` is False.
    """
    return CategoryDef(
        category=category,
        tier=tier,
        register=register,
        confidence_floor=None,
        auto_eligible=False,
        note=note,
    )


# ---------------------------------------------------------------------------
# THE registry — the FULL CLASSIFIER §2 / DESIGN §5 category table (v1).
# Ordering mirrors the §2 table top-to-bottom: tier-1 autonomous, then the
# tier-1 hard-refusals, then tier-2, then the tier-3 set.
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, CategoryDef] = {}


def _register(d: CategoryDef) -> None:
    _REGISTRY[d.category] = d


# --- tier-1 autonomous (CLASSIFIER §2 default tier 1, calm default) ----------
_register(_t1("price", CALM, note="live on-chain reads"))
_register(_t1("mechanics", CALM, note="KB-grounded"))
# trust: higher floor 0.92 (CLASSIFIER §2 per-category override), exact-match.
_register(_t1("trust", CALM, 0.92, note="KB-only; exact-match enforcement"))
_register(_t1("status", CALM, note="live on-chain"))
# greeting / meta_about_bot get LOWER floors (low-risk, CLASSIFIER §2).
_register(_t1("greeting", CALM, 0.70, note="always engage"))
_register(_t1("glossary", CALM, note="KB-grounded"))
_register(_t1("meta_about_bot", CALM, 0.75, note="pre-calibrated; reactive if skeptical"))
# catchphrase_repetition: tier-1 autonomous, calm — slow drip per Bible §IX.4. The
# per-client mantra-cadence / repeat-counter STATE lives with the C3.3 drafter
# (compose owns the cadence); the CATEGORY itself is tier-1 autonomous and present
# here. (Slow-drip cadence deferral, if any, is the drafter's §8 call — the category
# is NOT dropped.)
_register(
    _t1(
        "catchphrase_repetition",
        CALM,
        note="slow drip per Bible §IX.4 (cadence/repeat-counter owned by C3.3 drafter)",
    )
)

# --- tier-1 hard refusals (CLASSIFIER §2 "tier 1 (hard refusal)") ------------
# Always reactive register, never overridden (SAFETY §1 / VOICE §4). They are
# tier-1 (the bot handles them autonomously by REFUSING with the calibrated
# reactive wording), auto_eligible, default floor.
_register(_t1("price_prediction", REACTIVE, hard_refusal=True, note="always reactive register"))
_register(_t1("financial_advice", REACTIVE, hard_refusal=True, note="always reactive"))
_register(_t1("legal", REACTIVE, hard_refusal=True, note="always reactive"))

# --- tier-2 (drafter composes, human reviews) --------------------------------
_register(_t2_review("FUD_borderline", REACTIVE, note="operator reads intent"))
_register(
    _t2_review(
        "sentiment_negative", CALM, note="calm or reactive (operator decides); HITL first 30d"
    )
)
# partnership_unannounced: highest floor 0.95 (or always HITL) — leak risk.
_register(
    _t2_review(
        "partnership_unannounced",
        CALM,
        0.95,
        note="HITL ensures we don't leak; floor 0.95 or always HITL",
    )
)
_register(
    _t2_review("operational_complaint", CALM, note="operator verifies before answering")
)

# --- tier-2 operator-only routing (NEVER auto-reply; route to Arf) -----------
# conflict_detected: tier-2; routes to Arf, NULO does NOT respond publicly
# (CLASSIFIER §2 / FEATURE_INVENTORY E). register n/a (no public reply).
_register(
    _never_auto(
        "conflict_detected",
        TIER_HITL,
        None,
        note="routes to Arf for human handling; NULO does not respond publicly",
    )
)
# moderation_flag: tier-2 operator-only; routes to Arf + auto-silences the author
# (autocm_flagged_users). register n/a.
_register(
    _never_auto(
        "moderation_flag",
        TIER_HITL,
        None,
        note="operator-only; routes to mod (Arf); auto-silence the author",
    )
)

# --- tier-3 (needs founder; bot escalates, never autonomous) -----------------
# incident: tier-3, permanently HITL, war-room register (reactive). Also fires the
# `/incident-mode on` suggest (C3.8b). NEVER autonomous.
_register(
    _never_auto(
        "incident",
        TIER_ESCALATE,
        REACTIVE,
        note="permanently HITL; war-room register; also fires /incident-mode suggest",
    )
)
# The founder-only tier-3 set: register n/a, NO auto-reply (public reply
# suppressed; founder + on-call awareness). Routing owned by C3.8a.
_register(
    _never_auto("threat", TIER_ESCALATE, None, note="safety-critical; founder + on-call; no auto-reply")
)
_register(
    _never_auto("whale_inbound", TIER_ESCALATE, None, note="founder + on-call see; no auto-reply")
)
_register(
    _never_auto(
        "founder_voice_needed",
        TIER_ESCALATE,
        None,
        note="anything obviously needing a personal founder reply; no auto-reply",
    )
)


# Frozen public views (a copy so callers cannot mutate the registry).
CATEGORIES: tuple[str, ...] = tuple(_REGISTRY.keys())

#: the tier-1 autonomous categories whose presence the C3.4b exit asserts (incl.
#: the hard-refusal tier-1 set). Computed from the registry so it cannot drift.
TIER1_CATEGORIES: tuple[str, ...] = tuple(
    c for c, d in _REGISTRY.items() if d.tier == TIER_AUTONOMOUS
)

#: the full tier-3 set the C3.4b exit asserts present + marked no-auto.
TIER3_CATEGORIES: tuple[str, ...] = tuple(
    c for c, d in _REGISTRY.items() if d.tier == TIER_ESCALATE
)

#: the tier-1 hard-refusal categories (always-reactive, never-overridden).
HARD_REFUSAL_CATEGORIES: tuple[str, ...] = tuple(
    c for c, d in _REGISTRY.items() if d.hard_refusal
)


def get_category_def(category: str) -> Optional[CategoryDef]:
    """Return the registry :class:`CategoryDef` for ``category``, or None.

    A ``None`` return is the validation signal CLASSIFIER §6 names: a classifier
    that hallucinates a category not in the registry must NOT be trusted — the
    caller (the C3.4b tier classifier) defaults to tier-2 + calm (HITL) when this
    returns None.
    """
    return _REGISTRY.get(category)


def is_known_category(category: str) -> bool:
    """True iff ``category`` is in the registry (the §6 hallucination guard)."""
    return category in _REGISTRY


@dataclass(frozen=True)
class CategoryState:
    """The MERGED per-client × per-category view the C3.5a gate consumes.

    Overlays the runtime ``autocm_category_state`` row (if any) onto the registry
    default. ``state`` is the runtime autonomy state ('hitl' | 'auto'); when no
    runtime row exists the category defaults to ``'hitl'`` (the 058 column default
    and the safe-by-construction floor — a category only auto-sends after the C3.5a
    state machine promotes it). ``confidence_threshold`` is the runtime per-client
    override when a row exists, else the registry ``confidence_floor``.
    ``auto_eligible`` is the registry invariant — a never-auto category (tier-3 /
    conflict_detected / moderation_flag / incident) is never ``auto`` regardless of
    any runtime row.
    """

    category: str
    tier: int
    register: Optional[str]
    state: str  # 'hitl' | 'auto'
    confidence_threshold: Optional[float]
    auto_eligible: bool
    hard_refusal: bool
    sample_count: int = 0

    @property
    def is_auto(self) -> bool:
        """True iff this category is BOTH registry-auto-eligible AND runtime-'auto'.

        The two-condition gate: a never-auto category can never be ``is_auto`` even
        if a runtime row somehow says 'auto', and an auto-eligible category is only
        ``is_auto`` once its runtime state has been promoted to 'auto'.
        """
        return self.auto_eligible and self.state == "auto"


def resolve_category_state(
    conn: Connection, client_id: int, category: str
) -> Optional[CategoryState]:
    """Merge the registry default with the client's ``autocm_category_state`` row.

    Returns ``None`` for an unknown category (the §6 hallucination guard — the
    caller defaults to tier-2/HITL). For a known category:

      * the ``tier`` / ``register`` / ``auto_eligible`` / ``hard_refusal`` come from
        the static registry (per-client-independent routing invariants);
      * the runtime ``state`` / ``confidence_threshold`` / ``sample_count`` come from
        the ``autocm_category_state`` row when present; when absent they default to
        ``'hitl'`` / the registry ``confidence_floor`` / ``0`` — so a fresh client
        with no seeded rows is HITL-by-default everywhere (the safe floor).

    A never-auto registry category is forced to ``state='hitl'`` here even if a
    stray runtime row says 'auto', so the merged view can never make a tier-3 /
    conflict / moderation / incident category autonomous.
    """
    d = _REGISTRY.get(category)
    if d is None:
        return None

    row = conn.execute(
        text(
            "SELECT state, confidence_threshold, sample_count "
            "FROM autocm_category_state "
            "WHERE client_id = :client_id AND category = :category"
        ),
        {"client_id": client_id, "category": category},
    ).fetchone()

    if row is None:
        state = "hitl"
        threshold = d.confidence_floor
        sample_count = 0
    else:
        m = row._mapping
        state = m["state"]
        # runtime threshold wins when present; else fall back to the registry floor.
        threshold = (
            m["confidence_threshold"]
            if m["confidence_threshold"] is not None
            else d.confidence_floor
        )
        sample_count = m["sample_count"] or 0

    # never-auto categories can NEVER be 'auto', regardless of a stray runtime row.
    if not d.auto_eligible:
        state = "hitl"

    return CategoryState(
        category=d.category,
        tier=d.tier,
        register=d.register,
        state=state,
        confidence_threshold=threshold,
        auto_eligible=d.auto_eligible,
        hard_refusal=d.hard_refusal,
        sample_count=sample_count,
    )


__all__ = [
    # tier constants
    "TIER_AUTONOMOUS",
    "TIER_HITL",
    "TIER_ESCALATE",
    "RUNTIME_STATES",
    "DEFAULT_CONFIDENCE_FLOOR",
    # registry types + data
    "CategoryDef",
    "CATEGORIES",
    "TIER1_CATEGORIES",
    "TIER3_CATEGORIES",
    "HARD_REFUSAL_CATEGORIES",
    "get_category_def",
    "is_known_category",
    # merged runtime view
    "CategoryState",
    "resolve_category_state",
]
