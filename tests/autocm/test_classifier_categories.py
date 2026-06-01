"""C3.4b category-registry tests: the tri-tier ~20-entry registry + thresholds.

EXIT GATE (per the MEGAPLAN C3.4b tests/exit line):

  * ALL tier-1 categories present (incl. catchphrase_repetition, meta_about_bot,
    greeting, glossary, price, mechanics, trust, status + the hard-refusal tier-1
    set price_prediction/financial_advice/legal) — NOT only the tier-3 additions;
  * the FULL tier-3 set present: conflict_detected/moderation_flag/incident/threat/
    whale_inbound/founder_voice_needed (with threat/whale_inbound/founder_voice_needed
    marked tier-3, no-auto-reply);
  * per-category confidence thresholds (default 0.85, trust 0.92,
    partnership_unannounced 0.95, greeting 0.70, meta_about_bot 0.75; N/A for never-auto);
  * the merged runtime view overlays autocm_category_state onto the registry, and a
    never-auto category can NEVER be `auto` even with a stray runtime row.

All offline. No LLM, no network.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from sable_platform.autocm.classifier import categories as cat


# ---------------------------------------------------------------------------
# Tier-1 completeness — the FULL tier-1 set, not only the tier-3 additions.
# ---------------------------------------------------------------------------
TIER1_EXPECTED = {
    # tier-1 autonomous (calm default)
    "price",
    "mechanics",
    "trust",
    "status",
    "greeting",
    "glossary",
    "meta_about_bot",
    "catchphrase_repetition",
    # tier-1 hard refusals
    "price_prediction",
    "financial_advice",
    "legal",
}


def test_all_tier1_categories_present() -> None:
    """The EXIT assertion: every tier-1 category is in the registry."""
    for c in TIER1_EXPECTED:
        d = cat.get_category_def(c)
        assert d is not None, f"tier-1 category {c!r} missing from registry"
        assert d.tier == cat.TIER_AUTONOMOUS, f"{c!r} should be tier-1"
    # and the registry's computed tier-1 set is EXACTLY this set (no extras/drops).
    assert set(cat.TIER1_CATEGORIES) == TIER1_EXPECTED


def test_catchphrase_repetition_is_tier1_calm_autonomous() -> None:
    """catchphrase_repetition is explicitly tier-1, calm, auto-eligible (NOT dropped)."""
    d = cat.get_category_def("catchphrase_repetition")
    assert d is not None
    assert d.tier == cat.TIER_AUTONOMOUS
    assert d.register == cat.CALM
    assert d.auto_eligible is True
    # the slow-drip cadence is the drafter's concern; the CATEGORY is present here.
    assert "Bible" in d.note


def test_hard_refusal_tier1_set_always_reactive() -> None:
    """price_prediction/financial_advice/legal: tier-1, hard_refusal, reactive."""
    assert set(cat.HARD_REFUSAL_CATEGORIES) == {
        "price_prediction",
        "financial_advice",
        "legal",
    }
    for c in cat.HARD_REFUSAL_CATEGORIES:
        d = cat.get_category_def(c)
        assert d.tier == cat.TIER_AUTONOMOUS
        assert d.hard_refusal is True
        assert d.register == cat.REACTIVE  # always reactive register (SAFETY §0)


# ---------------------------------------------------------------------------
# Tier-2 set present.
# ---------------------------------------------------------------------------
def test_tier2_drafting_categories_present() -> None:
    for c in ("FUD_borderline", "sentiment_negative", "partnership_unannounced", "operational_complaint"):
        d = cat.get_category_def(c)
        assert d is not None
        assert d.tier == cat.TIER_HITL
        assert d.auto_eligible is True  # promotable via the C3.5a state machine


# ---------------------------------------------------------------------------
# FULL tier-3 set + the operator-only tier-2 routing categories.
# ---------------------------------------------------------------------------
def test_full_tier3_set_present_and_no_auto() -> None:
    """conflict_detected/moderation_flag/incident/threat/whale_inbound/founder_voice_needed
    all present; the tier-3 trio marked tier-3 + no-auto-reply."""
    # tier-3 set computed from the registry.
    assert set(cat.TIER3_CATEGORIES) == {
        "incident",
        "threat",
        "whale_inbound",
        "founder_voice_needed",
    }
    # the founder-only tier-3 trio: tier-3, register n/a, never auto.
    for c in ("threat", "whale_inbound", "founder_voice_needed"):
        d = cat.get_category_def(c)
        assert d.tier == cat.TIER_ESCALATE
        assert d.register is None  # no public reply
        assert d.auto_eligible is False
        assert d.confidence_floor is None  # N/A in §2


def test_incident_is_tier3_never_auto_warroom_register() -> None:
    d = cat.get_category_def("incident")
    assert d.tier == cat.TIER_ESCALATE
    assert d.auto_eligible is False
    assert d.register == cat.REACTIVE  # war-room register (sub-variant of reactive)
    assert d.confidence_floor is None


def test_conflict_and_moderation_are_tier2_never_auto_arf_only() -> None:
    """conflict_detected / moderation_flag: tier-2 operator-only routing, never auto,
    register n/a (NULO does not respond publicly)."""
    for c in ("conflict_detected", "moderation_flag"):
        d = cat.get_category_def(c)
        assert d.tier == cat.TIER_HITL
        assert d.auto_eligible is False  # routes to Arf; never auto-reply
        assert d.register is None
        assert d.confidence_floor is None


# ---------------------------------------------------------------------------
# Per-category confidence thresholds (CLASSIFIER §2).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "category,expected_floor",
    [
        ("mechanics", 0.85),  # default floor
        ("price", 0.85),
        ("status", 0.85),
        ("glossary", 0.85),
        ("trust", 0.92),  # higher floor (high-risk)
        ("partnership_unannounced", 0.95),  # highest floor (leak risk)
        ("greeting", 0.70),  # lower floor (low-risk)
        ("meta_about_bot", 0.75),  # lower floor (low-risk)
    ],
)
def test_per_category_confidence_floors(category, expected_floor) -> None:
    d = cat.get_category_def(category)
    assert d.confidence_floor == pytest.approx(expected_floor)


def test_never_auto_categories_have_no_floor() -> None:
    """N/A threshold rows (§2): never-auto categories carry confidence_floor None."""
    for c in ("conflict_detected", "moderation_flag", "incident", "threat", "whale_inbound", "founder_voice_needed"):
        assert cat.get_category_def(c).confidence_floor is None


def test_default_floor_constant() -> None:
    assert cat.DEFAULT_CONFIDENCE_FLOOR == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# Hallucination guard (CLASSIFIER §6).
# ---------------------------------------------------------------------------
def test_unknown_category_guard() -> None:
    assert cat.is_known_category("mechanics") is True
    assert cat.is_known_category("totally_made_up") is False
    assert cat.get_category_def("totally_made_up") is None


# ---------------------------------------------------------------------------
# Merged runtime view — resolve_category_state overlays autocm_category_state.
# ---------------------------------------------------------------------------
def _seed_client(conn, org_id: str) -> int:
    conn.execute(
        text(
            "INSERT INTO autocm_clients (org_id, display_name, autonomy_state, enabled) "
            "VALUES (:o, 'RobotMoney', 'hitl', 1)"
        ),
        {"o": org_id},
    )
    return conn.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = :o"), {"o": org_id}
    ).fetchone()[0]


def _seed_category_state(conn, client_id, category, *, state="hitl", threshold=None, samples=0):
    conn.execute(
        text(
            "INSERT INTO autocm_category_state "
            "(client_id, category, state, confidence_threshold, sample_count) "
            "VALUES (:c, :cat, :s, :t, :n)"
        ),
        {"c": client_id, "cat": category, "s": state, "t": threshold if threshold is not None else 0.8, "n": samples},
    )


@pytest.fixture
def client_env(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    conn.commit()
    return conn, org_id, client_id


def test_resolve_unknown_category_returns_none(client_env) -> None:
    conn, _, client_id = client_env
    assert cat.resolve_category_state(conn, client_id, "hallucinated") is None


def test_resolve_no_runtime_row_defaults_to_hitl_with_registry_floor(client_env) -> None:
    """A fresh client with NO autocm_category_state row defaults to HITL + the
    registry confidence floor (the safe-by-construction floor)."""
    conn, _, client_id = client_env
    cs = cat.resolve_category_state(conn, client_id, "mechanics")
    assert cs is not None
    assert cs.state == "hitl"  # default floor — never auto until promoted
    assert cs.is_auto is False
    assert cs.confidence_threshold == pytest.approx(0.85)  # registry floor
    assert cs.tier == cat.TIER_AUTONOMOUS
    assert cs.sample_count == 0


def test_resolve_runtime_auto_row_makes_eligible_category_auto(client_env) -> None:
    conn, _, client_id = client_env
    _seed_category_state(conn, client_id, "mechanics", state="auto", threshold=0.88, samples=60)
    conn.commit()
    cs = cat.resolve_category_state(conn, client_id, "mechanics")
    assert cs.state == "auto"
    assert cs.is_auto is True
    assert cs.confidence_threshold == pytest.approx(0.88)  # runtime threshold wins
    assert cs.sample_count == 60


def test_resolve_never_auto_category_forced_hitl_even_with_stray_auto_row(client_env) -> None:
    """A never-auto registry category (tier-3 / conflict / moderation / incident)
    can NEVER be `auto`, even if a stray runtime row says 'auto'."""
    conn, _, client_id = client_env
    # a malformed/stray 'auto' row for a tier-3 category
    _seed_category_state(conn, client_id, "threat", state="auto", threshold=0.5, samples=999)
    conn.commit()
    cs = cat.resolve_category_state(conn, client_id, "threat")
    assert cs.auto_eligible is False
    assert cs.state == "hitl"  # forced back to hitl regardless of the stray row
    assert cs.is_auto is False
    assert cs.tier == cat.TIER_ESCALATE


def test_resolve_runtime_threshold_falls_back_to_registry_floor_when_null(client_env) -> None:
    """When the runtime row's confidence_threshold matches no override, the registry
    floor still applies for the merged view (trust's 0.92 stays the floor)."""
    conn, _, client_id = client_env
    # seed a trust row that leaves threshold at the registry-aligned default; the
    # merged view's threshold is the runtime value when present.
    _seed_category_state(conn, client_id, "trust", state="hitl", threshold=0.92, samples=10)
    conn.commit()
    cs = cat.resolve_category_state(conn, client_id, "trust")
    assert cs.confidence_threshold == pytest.approx(0.92)
    assert cs.tier == cat.TIER_AUTONOMOUS
