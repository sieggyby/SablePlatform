"""C3.5a — autonomy state machine (DESIGN §7): edit_diff_ratio, promotion gate,
auto-demotion triggers (1) rolling-7d + (3) safety-slip.

The load-bearing quantity is :func:`edit_diff_ratio` (token-levenshtein / max
token length, >0.30 == heavy edit == a rejection). Two boundary fixtures pin the
30% threshold crossing (one just BELOW asserted clean, one just ABOVE asserted
rejection) so the auditor can check the threshold directly (MEGAPLAN C3.5a).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from sable_platform.autocm.gate import autonomy
from sable_platform.autocm.gate.autonomy import (
    AUTO_DEMOTE_RATE,
    HEAVY_EDIT_THRESHOLD,
    MIN_CLEAN_APPROVAL_RATE,
    MIN_SAMPLES,
    demote_on_safety_slip,
    edit_diff_ratio,
    gather_review_stats,
    is_clean_approval,
    is_heavy_edit,
    promote_category,
    promotion_gate,
    review_is_clean_row,
    sweep_auto_demotions,
)
from sable_platform.db.audit import list_audit_log


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_client(conn, org_id):
    conn.execute(
        text(
            "INSERT INTO autocm_clients (org_id, display_name, autonomy_state, enabled) "
            "VALUES (:o, 'RM', 'hitl', 1)"
        ),
        {"o": org_id},
    )
    return conn.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = :o"), {"o": org_id}
    ).fetchone()[0]


def _seed_category_state(conn, client_id, category, state="hitl"):
    conn.execute(
        text(
            "INSERT INTO autocm_category_state (client_id, category, state) "
            "VALUES (:c, :cat, :s)"
        ),
        {"c": client_id, "cat": category, "s": state},
    )


def _seed_review(
    conn, client_id, category, decision, *, edit_diff_size=0.0, is_clean, reviewed_at=None
):
    """Insert a draft + its review row in ``category`` with the given clean flag."""
    conn.execute(
        text(
            "INSERT INTO autocm_drafts (client_id, category, status) "
            "VALUES (:c, :cat, 'approved')"
        ),
        {"c": client_id, "cat": category},
    )
    draft_id = conn.execute(
        text("SELECT id FROM autocm_drafts ORDER BY id DESC LIMIT 1")
    ).fetchone()[0]
    params = {
        "d": draft_id,
        "c": client_id,
        "dec": decision,
        "eds": edit_diff_size,
        "clean": 1 if is_clean else 0,
    }
    sql = (
        "INSERT INTO autocm_reviews "
        "(draft_id, client_id, decision, edit_diff_size, is_clean_approval"
    )
    if reviewed_at is not None:
        sql += ", reviewed_at) VALUES (:d, :c, :dec, :eds, :clean, :ra)"
        params["ra"] = reviewed_at
    else:
        sql += ") VALUES (:d, :c, :dec, :eds, :clean)"
    conn.execute(text(sql), params)
    return draft_id


# ---------------------------------------------------------------------------
# edit_diff_ratio — the PINNED quantity
# ---------------------------------------------------------------------------
def test_edit_diff_ratio_identical_is_zero():
    assert edit_diff_ratio("the vault deploys treasury capital", "the vault deploys treasury capital") == 0.0


def test_edit_diff_ratio_both_empty_is_zero():
    assert edit_diff_ratio("", "") == 0.0
    assert edit_diff_ratio(None, None) == 0.0


def test_edit_diff_ratio_full_rewrite_is_one():
    assert edit_diff_ratio("a b c", "x y z") == 1.0


def test_edit_diff_ratio_one_of_three_substituted():
    # one substitution over a 3-token max → 1/3.
    assert round(edit_diff_ratio("a b c", "a b X"), 6) == round(1 / 3, 6)


def test_edit_diff_ratio_insertion_uses_max_denominator():
    # draft 3 tokens, final 4 tokens, one insertion → distance 1 / max(3,4)=4.
    assert edit_diff_ratio("a b c", "a b c d") == 0.25


# --- the TWO pinned boundary fixtures (30% threshold crossing) -------------
def test_boundary_just_below_threshold_is_clean():
    """A ~28.6% edit (2 of 7 tokens changed) is BELOW 0.30 → asserted CLEAN.

    7-token draft, 2 substitutions → 2/7 ≈ 0.2857 < 0.30. The drafter's text and
    the operator's light touch-up; this is a clean approval (NOT a heavy edit).
    """
    draft = "the robotmoney vault deploys treasury capital safely"
    final = "the robotmoney vault deploys liquidity capital quickly"  # 2 tokens changed
    ratio = edit_diff_ratio(draft, final)
    assert round(ratio, 4) == round(2 / 7, 4)
    assert ratio < HEAVY_EDIT_THRESHOLD
    assert not is_heavy_edit(ratio)
    assert is_clean_approval("edit", draft_text=draft, final_text=final) is True


def test_boundary_just_above_threshold_is_rejection():
    """A ~42.9% edit (3 of 7 tokens changed) is ABOVE 0.30 → asserted REJECTION.

    7-token draft, 3 substitutions → 3/7 ≈ 0.4286 > 0.30 → a heavy edit, which
    counts as a rejection for clean-approval purposes.
    """
    draft = "the robotmoney vault deploys treasury capital safely"
    final = "the robotmoney protocol deploys liquidity reserves safely"  # 3 changed
    ratio = edit_diff_ratio(draft, final)
    assert round(ratio, 4) == round(3 / 7, 4)
    assert ratio > HEAVY_EDIT_THRESHOLD
    assert is_heavy_edit(ratio)
    assert is_clean_approval("edit", draft_text=draft, final_text=final) is False


def test_is_clean_approval_approve_always_clean():
    assert is_clean_approval("approve") is True


def test_is_clean_approval_reject_and_punt_never_clean():
    assert is_clean_approval("reject") is False
    assert is_clean_approval("punt_to_founder") is False


def test_is_clean_approval_edit_without_text_is_not_clean():
    # cannot prove the edit was light → conservatively not clean.
    assert is_clean_approval("edit") is False


def test_review_is_clean_row_mirrors_stored_ratio():
    assert review_is_clean_row("approve", 0.9) is True  # approve ignores ratio
    assert review_is_clean_row("edit", 0.10) is True
    assert review_is_clean_row("edit", 0.30) is True  # exactly 0.30 is NOT heavy
    assert review_is_clean_row("edit", 0.31) is False
    assert review_is_clean_row("reject", 0.0) is False


# ---------------------------------------------------------------------------
# promotion_gate — DESIGN §7 (ALL conditions)
# ---------------------------------------------------------------------------
def test_promotion_gate_all_conditions_met():
    v = promotion_gate(
        sample_count=50,
        clean_approval_count=45,  # exactly 0.90
        hard_refusal_violations=0,
        safety_breaches=0,
        operator_sign_off=True,
    )
    assert v.promote is True
    assert v.reasons == []
    assert v.clean_approval_rate == 0.90


def test_promotion_gate_below_sample_floor():
    v = promotion_gate(
        sample_count=49,
        clean_approval_count=49,
        hard_refusal_violations=0,
        safety_breaches=0,
        operator_sign_off=True,
    )
    assert v.promote is False
    assert any("sample_count" in r for r in v.reasons)


def test_promotion_gate_below_clean_rate():
    v = promotion_gate(
        sample_count=100,
        clean_approval_count=89,  # 0.89 < 0.90
        hard_refusal_violations=0,
        safety_breaches=0,
        operator_sign_off=True,
    )
    assert v.promote is False
    assert any("clean_approval_rate" in r for r in v.reasons)


def test_promotion_gate_blocks_on_safety_breach():
    v = promotion_gate(
        sample_count=100,
        clean_approval_count=100,
        hard_refusal_violations=0,
        safety_breaches=1,
        operator_sign_off=True,
    )
    assert v.promote is False
    assert any("safety breach" in r for r in v.reasons)


def test_promotion_gate_blocks_on_hard_refusal_violation():
    v = promotion_gate(
        sample_count=100,
        clean_approval_count=100,
        hard_refusal_violations=2,
        safety_breaches=0,
        operator_sign_off=True,
    )
    assert v.promote is False
    assert any("hard-refusal" in r for r in v.reasons)


def test_promotion_gate_requires_operator_sign_off():
    v = promotion_gate(
        sample_count=100,
        clean_approval_count=100,
        hard_refusal_violations=0,
        safety_breaches=0,
        operator_sign_off=False,
    )
    assert v.promote is False
    assert any("sign-off" in r for r in v.reasons)


# ---------------------------------------------------------------------------
# gather_review_stats — reads 058 tables
# ---------------------------------------------------------------------------
def test_gather_review_stats_counts_clean_and_samples(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    for _ in range(45):
        _seed_review(conn, client_id, "mechanics", "approve", is_clean=True)
    for _ in range(5):
        _seed_review(conn, client_id, "mechanics", "reject", is_clean=False)
    conn.commit()
    stats = gather_review_stats(conn, client_id, "mechanics")
    assert stats.sample_count == 50
    assert stats.clean_approval_count == 45
    assert stats.hard_refusal_violations == 0
    assert stats.safety_breaches == 0


def test_gather_review_stats_counts_injection_blocked_as_breach(sa_org):
    """DESIGN §7: an injection ATTEMPT (audit action='injection_blocked') counts as
    a safety breach for the promotion gate, with no injection exemption — so a
    category with a logged injection breach is blocked from promotion.
    """
    from sable_platform.autocm.gate.safety import SafetyVerdict, audit_safety_block
    from sable_platform._vendor.sable_pulse_core import RefusalMatch

    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    # an injection match → audited under action='injection_blocked'.
    verdict = SafetyVerdict(
        tripped=True,
        match=RefusalMatch(
            category="prompt_injection",
            kind="injection",
            trigger="ignore previous instructions",
            register="reactive",
        ),
    )
    audit_safety_block(conn, verdict, org_id=org_id, category="mechanics")
    conn.commit()

    stats = gather_review_stats(conn, client_id, "mechanics")
    assert stats.safety_breaches == 1
    # the gate must block promotion on that breach (even with otherwise-clean stats).
    gate = promotion_gate(
        sample_count=50,
        clean_approval_count=50,
        hard_refusal_violations=0,
        safety_breaches=stats.safety_breaches,
        operator_sign_off=True,
    )
    assert gate.promote is False
    assert any("safety breach" in r for r in gate.reasons)


def test_gather_review_stats_windowed_breach_counts_same_window_audit_row(sa_org):
    """Regression (low fix): a windowed breach query (``since`` set) must COUNT a
    same-window injection_blocked audit row. ``audit_log.timestamp`` is written by
    the column DEFAULT in space-separated form (``YYYY-MM-DD HH:MM:SS``), while
    ``since`` is the _iso_z ``...T...Z`` form; the two are not lexically comparable
    (space 0x20 < 'T' 0x54). Before the fix, ``timestamp >= :since`` silently
    dropped the row. The fix normalizes both sides to space form so the row counts.
    """
    from sable_platform.autocm.gate.safety import SafetyVerdict, audit_safety_block
    from sable_platform._vendor.sable_pulse_core import RefusalMatch

    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    verdict = SafetyVerdict(
        tripped=True,
        match=RefusalMatch(
            category="prompt_injection",
            kind="injection",
            trigger="ignore previous instructions",
            register="reactive",
        ),
    )
    # the audit row gets a "now-ish" default timestamp (space form).
    audit_safety_block(conn, verdict, org_id=org_id, category="mechanics")
    conn.commit()

    # a window that comfortably contains "now" (since well in the past).
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stats = gather_review_stats(conn, client_id, "mechanics", since=since)
    assert stats.safety_breaches == 1


def test_gather_review_stats_hard_refusal_violations(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    # legal is a hard-refusal category; a non-clean review is a refusal violation.
    _seed_review(conn, client_id, "legal", "approve", is_clean=True)
    _seed_review(conn, client_id, "legal", "edit", edit_diff_size=0.5, is_clean=False)
    _seed_review(conn, client_id, "legal", "reject", is_clean=False)
    conn.commit()
    stats = gather_review_stats(conn, client_id, "legal")
    assert stats.sample_count == 3
    assert stats.clean_approval_count == 1
    assert stats.hard_refusal_violations == 2  # the non-clean ones


# ---------------------------------------------------------------------------
# promote_category — gate + flip + audit
# ---------------------------------------------------------------------------
def test_promote_category_flips_and_audits_when_passing(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_category_state(conn, client_id, "mechanics", "hitl")
    for _ in range(45):
        _seed_review(conn, client_id, "mechanics", "approve", is_clean=True)
    for _ in range(5):
        _seed_review(conn, client_id, "mechanics", "edit", edit_diff_size=0.1, is_clean=True)
    conn.commit()

    verdict = promote_category(
        conn, client_id, "mechanics", actor="op1", operator_sign_off=True, org_id=org_id
    )
    assert verdict.promote is True
    state = conn.execute(
        text("SELECT state FROM autocm_category_state WHERE client_id = :c AND category = 'mechanics'"),
        {"c": client_id},
    ).fetchone()[0]
    assert state == "auto"
    rows = list_audit_log(conn, org_id=org_id, action=autonomy.ACTION_PROMOTE)
    assert len(rows) == 1


def test_promote_category_no_flip_when_failing(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_category_state(conn, client_id, "mechanics", "hitl")
    for _ in range(10):  # under the 50 floor
        _seed_review(conn, client_id, "mechanics", "approve", is_clean=True)
    conn.commit()
    verdict = promote_category(
        conn, client_id, "mechanics", actor="op1", operator_sign_off=True, org_id=org_id
    )
    assert verdict.promote is False
    state = conn.execute(
        text("SELECT state FROM autocm_category_state WHERE client_id = :c AND category = 'mechanics'"),
        {"c": client_id},
    ).fetchone()[0]
    assert state == "hitl"
    assert list_audit_log(conn, org_id=org_id, action=autonomy.ACTION_PROMOTE) == []


@pytest.mark.parametrize("never_auto_category", ["conflict_detected", "incident"])
def test_promote_category_refuses_never_auto_category(sa_org, never_auto_category):
    """DESIGN §7 / SAFETY §5: a never-auto category (auto_eligible=False) with 50+
    clean reviews + operator sign-off must NOT have its state flipped to 'auto' and
    must NOT log an autonomy_promoted row — even though none of these are
    hard-refusal categories (so the gate raises no hard-refusal violation for them).
    The persisted state and the audit trail must not lie.
    """
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_category_state(conn, client_id, never_auto_category, "hitl")
    # a textbook-passing review history (would promote a normal tier-2 category).
    for _ in range(50):
        _seed_review(conn, client_id, never_auto_category, "approve", is_clean=True)
    conn.commit()

    verdict = promote_category(
        conn, client_id, never_auto_category,
        actor="op1", operator_sign_off=True, org_id=org_id,
    )
    assert verdict.promote is False
    assert any("never-auto" in r for r in verdict.reasons)
    # state stays hitl, no promotion audit row written.
    state = conn.execute(
        text(
            "SELECT state FROM autocm_category_state "
            "WHERE client_id = :c AND category = :cat"
        ),
        {"c": client_id, "cat": never_auto_category},
    ).fetchone()[0]
    assert state == "hitl"
    assert list_audit_log(conn, org_id=org_id, action=autonomy.ACTION_PROMOTE) == []


def test_promote_category_refuses_unknown_category(sa_org):
    """DESIGN §7 / SAFETY §5 (low fix): an UNKNOWN category (not in the registry —
    get_category_def is None) must be treated like never-auto. Even with 50+ clean
    reviews + operator sign-off it must NOT flip state to 'auto' and must NOT write
    an autonomy_promoted row — a hallucinated category's persisted state + audit
    trail must not lie (the read side already routes it to HITL).
    """
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    bogus = "totally_made_up_category"
    _seed_category_state(conn, client_id, bogus, "hitl")
    for _ in range(50):
        _seed_review(conn, client_id, bogus, "approve", is_clean=True)
    conn.commit()

    verdict = promote_category(
        conn, client_id, bogus, actor="op1", operator_sign_off=True, org_id=org_id
    )
    assert verdict.promote is False
    assert any("unknown_category" in r for r in verdict.reasons)
    state = conn.execute(
        text(
            "SELECT state FROM autocm_category_state "
            "WHERE client_id = :c AND category = :cat"
        ),
        {"c": client_id, "cat": bogus},
    ).fetchone()[0]
    assert state == "hitl"
    assert list_audit_log(conn, org_id=org_id, action=autonomy.ACTION_PROMOTE) == []


def test_promote_category_refuses_while_frozen(sa_org):
    """SAFETY §6: a category under an active freeze_until is NOT promoted — a
    promotion may not bank through the freeze window. State stays hitl, no audit.
    """
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_category_state(conn, client_id, "mechanics", "hitl")
    # active freeze well into the future.
    future = (datetime.now(timezone.utc) + timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        text(
            "UPDATE autocm_category_state SET freeze_until = :f "
            "WHERE client_id = :c AND category = 'mechanics'"
        ),
        {"f": future, "c": client_id},
    )
    for _ in range(50):
        _seed_review(conn, client_id, "mechanics", "approve", is_clean=True)
    conn.commit()

    verdict = promote_category(
        conn, client_id, "mechanics", actor="op1", operator_sign_off=True, org_id=org_id
    )
    assert verdict.promote is False
    assert any("frozen" in r for r in verdict.reasons)
    state = conn.execute(
        text("SELECT state FROM autocm_category_state WHERE client_id = :c AND category = 'mechanics'"),
        {"c": client_id},
    ).fetchone()[0]
    assert state == "hitl"
    assert list_audit_log(conn, org_id=org_id, action=autonomy.ACTION_PROMOTE) == []


# ---------------------------------------------------------------------------
# demote_on_safety_slip — DESIGN §7 trigger (3)
# ---------------------------------------------------------------------------
def test_demote_on_safety_slip_flips_auto_to_hitl(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_category_state(conn, client_id, "mechanics", "auto")
    conn.commit()
    demoted = demote_on_safety_slip(conn, client_id, "mechanics", org_id=org_id)
    assert demoted is True
    state = conn.execute(
        text("SELECT state FROM autocm_category_state WHERE client_id = :c AND category = 'mechanics'"),
        {"c": client_id},
    ).fetchone()[0]
    assert state == "hitl"
    rows = list_audit_log(conn, org_id=org_id, action=autonomy.ACTION_DEMOTE_SAFETY)
    assert len(rows) == 1


def test_demote_on_safety_slip_noop_when_already_hitl(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_category_state(conn, client_id, "mechanics", "hitl")
    conn.commit()
    assert demote_on_safety_slip(conn, client_id, "mechanics", org_id=org_id) is False
    assert list_audit_log(conn, org_id=org_id, action=autonomy.ACTION_DEMOTE_SAFETY) == []


# ---------------------------------------------------------------------------
# sweep_auto_demotions — DESIGN §7 trigger (1), rolling-7d < 0.85
# ---------------------------------------------------------------------------
def test_sweep_demotes_category_below_rolling_threshold(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_category_state(conn, client_id, "mechanics", "auto")
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    inside = _iso(now - timedelta(days=1))  # inside the 7d window
    # 10 reviews in-window, 8 clean → 0.80 < 0.85.
    for _ in range(8):
        _seed_review(conn, client_id, "mechanics", "approve", is_clean=True, reviewed_at=inside)
    for _ in range(2):
        _seed_review(conn, client_id, "mechanics", "reject", is_clean=False, reviewed_at=inside)
    conn.commit()

    demoted = sweep_auto_demotions(conn, client_id, now=now, org_id=org_id)
    assert [d.category for d in demoted] == ["mechanics"]
    assert round(demoted[0].clean_approval_rate, 4) == 0.80
    state = conn.execute(
        text("SELECT state FROM autocm_category_state WHERE client_id = :c AND category = 'mechanics'"),
        {"c": client_id},
    ).fetchone()[0]
    assert state == "hitl"
    rows = list_audit_log(conn, org_id=org_id, action=autonomy.ACTION_DEMOTE_ROLLING)
    assert len(rows) == 1


def test_sweep_keeps_category_at_or_above_threshold(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_category_state(conn, client_id, "mechanics", "auto")
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    inside = _iso(now - timedelta(days=1))
    # 9 of 10 clean → 0.90 >= 0.85 → not demoted.
    for _ in range(9):
        _seed_review(conn, client_id, "mechanics", "approve", is_clean=True, reviewed_at=inside)
    _seed_review(conn, client_id, "mechanics", "reject", is_clean=False, reviewed_at=inside)
    conn.commit()
    demoted = sweep_auto_demotions(conn, client_id, now=now, org_id=org_id)
    assert demoted == []
    state = conn.execute(
        text("SELECT state FROM autocm_category_state WHERE client_id = :c AND category = 'mechanics'"),
        {"c": client_id},
    ).fetchone()[0]
    assert state == "auto"


def test_sweep_ignores_reviews_outside_window(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_category_state(conn, client_id, "mechanics", "auto")
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    old = _iso(now - timedelta(days=30))  # outside the 7d window
    # 10 bad reviews but ALL 30 days ago → 0 in-window samples → not swept.
    for _ in range(10):
        _seed_review(conn, client_id, "mechanics", "reject", is_clean=False, reviewed_at=old)
    conn.commit()
    # default min_samples=1 → 0 in-window samples means the category is skipped.
    demoted = sweep_auto_demotions(conn, client_id, now=now, org_id=org_id)
    assert demoted == []
    state = conn.execute(
        text("SELECT state FROM autocm_category_state WHERE client_id = :c AND category = 'mechanics'"),
        {"c": client_id},
    ).fetchone()[0]
    assert state == "auto"


def test_sweep_only_touches_auto_categories(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_category_state(conn, client_id, "mechanics", "hitl")  # already hitl
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    inside = _iso(now - timedelta(days=1))
    for _ in range(10):
        _seed_review(conn, client_id, "mechanics", "reject", is_clean=False, reviewed_at=inside)
    conn.commit()
    demoted = sweep_auto_demotions(conn, client_id, now=now, org_id=org_id)
    assert demoted == []  # a hitl category is not a sweep candidate


def test_threshold_constants_match_design_seven():
    # the DESIGN §7 numbers are pinned as module constants.
    assert MIN_SAMPLES == 50
    assert MIN_CLEAN_APPROVAL_RATE == 0.90
    assert HEAVY_EDIT_THRESHOLD == 0.30
    assert AUTO_DEMOTE_RATE == 0.85
