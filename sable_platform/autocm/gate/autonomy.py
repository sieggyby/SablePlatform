"""Autonomy state machine + auto-demotion (MEGAPLAN C3.5a — DESIGN §7).

The bidirectional per-client × per-category autonomy controller. Two halves:

**Promotion (HITL → auto).** :func:`promotion_gate` enforces the DESIGN §7
threshold — a category flips to ``auto`` ONLY when ALL of:

  * ``sample_count >= 50`` (``MIN_SAMPLES``) — enough HITL drafts reviewed;
  * ``clean_approval_rate >= 0.90`` (``MIN_CLEAN_APPROVAL_RATE``) — a heavy edit
    (``edit_diff_ratio > 0.30``) counts as a REJECTION, not an approval;
  * ZERO hard-refusal violations on the category's reviewed drafts;
  * ZERO safety breaches recorded for the category;
  * the operator has signed off (``operator_sign_off=True``).

:func:`promote_category` runs that gate and, only if it passes, flips the
``autocm_category_state`` row to ``auto`` + writes an audit row. The HITL_UX §6
``/promote`` operator command (C3.5c) returns this gate result verbatim.

**The load-bearing quantity — :func:`edit_diff_ratio`.** DESIGN.md:252 calls it
"token-diff", HITL_UX.md:58 "token edit distance >30%", DIGEST.md:109
``edit_diff_size > 30%`` — three names, ONE quantity::

    edit_diff_ratio = token_levenshtein(tok(draft), tok(final))
                      / max(len(tok(draft)), len(tok(final)))

tokenizer = whitespace-split tokens. A review counts as a REJECTION (heavy edit)
when ``edit_diff_ratio > 0.30`` — i.e. a "clean approval" is an ``approve`` (or an
``edit`` whose ratio is ``<= 0.30``). Reject / punt are never clean.

**Auto-demotion (auto → HITL).** This chunk owns 2 of the 4 DESIGN §7 demotion
triggers (the other two are cross-ref obligations on C3.5c `/demote` and C3.8a):

  * **trigger (1)** the rolling-7d clean-approval ``< 0.85``
    (``AUTO_DEMOTE_RATE``) → :func:`sweep_auto_demotions` (the scheduled
    WorkflowRunner sweep wrapper lives in
    ``workflows/builtins/autocm_autonomy_sweep.py``); writes an audit row per
    auto-demotion (autonomy is bidirectional, not promote-only);
  * **trigger (3)** a safety-gate violation that slips through on an ``auto``
    category → :func:`demote_on_safety_slip` immediately flips it to HITL + audit
    (wired from ``gate/safety``).

All timestamps are computed in Python as UTC ISO-8601 ``...Z`` and bound as
parameters (never ``strftime('now')``), so the rolling-window SQL is
dialect-agnostic and runs unchanged on the live Postgres pool — matching the
``relay/db.py`` / ``autocm/db.py`` contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.db.audit import log_audit

# ---------------------------------------------------------------------------
# DESIGN §7 thresholds (the load-bearing constants)
# ---------------------------------------------------------------------------
#: minimum reviewed HITL drafts before a category is promotion-eligible.
MIN_SAMPLES = 50
#: minimum clean-approval rate (heavy edit counts as rejection) for promotion.
MIN_CLEAN_APPROVAL_RATE = 0.90
#: a token edit-diff ABOVE this ratio is a "heavy edit" == a rejection.
HEAVY_EDIT_THRESHOLD = 0.30
#: rolling-7d clean-approval BELOW this auto-demotes an `auto` category to HITL.
AUTO_DEMOTE_RATE = 0.85
#: the rolling window (days) for the auto-demotion sweep (trigger 1).
ROLLING_WINDOW_DAYS = 7

# log_audit verbs (audit-everything convention; source="sable-autocm").
AUDIT_SOURCE = "sable-autocm"
ACTION_PROMOTE = "autonomy_promoted"
ACTION_DEMOTE_ROLLING = "autonomy_auto_demoted_rolling7d"
ACTION_DEMOTE_SAFETY = "autonomy_demoted_safety_slip"


# ---------------------------------------------------------------------------
# Clock seam (injectable; tests pin the rolling window deterministically)
# ---------------------------------------------------------------------------
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(dt: datetime) -> str:
    """Render a datetime to the 058 TEXT timestamp form (``...Z``, no micros)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_space(dt: datetime) -> str:
    """Render a datetime to the space-separated audit-log form (no T/Z, no micros).

    ``audit_log.timestamp`` is written by the column DEFAULT
    (018_audit_log.sql ``datetime('now')`` on SQLite / ``func.now()`` on Postgres),
    which renders ``YYYY-MM-DD HH:MM:SS`` (space-separated, no ``T``/``Z``) — NOT
    the :func:`_iso_z` ``...T...Z`` form the autocm convention binds elsewhere.
    The two TEXT forms are not lexically comparable (space ``0x20`` < ``T`` ``0x54``),
    so a windowed breach filter must compare against a ``since`` rendered in THIS
    form (after normalizing any ``T``/``Z`` in the stored value away). See
    :func:`gather_review_stats`.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_audit_ts(ts: str) -> str:
    """Normalize a ``...T...Z`` (or already space-separated) bound to space form.

    The dialect-agnostic mirror of the SQL ``REPLACE(REPLACE(.,'T',' '),'Z','')``
    applied to ``audit_log.timestamp`` so the windowed-breach comparison in
    :func:`gather_review_stats` compares like-for-like.
    """
    return (ts or "").replace("T", " ").replace("Z", "")


# ---------------------------------------------------------------------------
# edit_diff_ratio — THE quantity that drives clean_approval_rate (PINNED)
# ---------------------------------------------------------------------------
def _tok(text_value: Optional[str]) -> List[str]:
    """Whitespace-split tokenizer (the PINNED tokenizer for edit_diff_ratio)."""
    return (text_value or "").split()


def _token_levenshtein(a: Sequence[str], b: Sequence[str]) -> int:
    """Levenshtein (edit) distance over token sequences.

    Classic two-row DP, O(len(a)*len(b)) time / O(len(b)) space. Returns the
    number of single-token insertions/deletions/substitutions to turn ``a`` into
    ``b``.
    """
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ai = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ai == b[j - 1] else 1
            cur[j] = min(
                prev[j] + 1,        # deletion
                cur[j - 1] + 1,     # insertion
                prev[j - 1] + cost,  # substitution / match
            )
        prev = cur
    return prev[lb]


def edit_diff_ratio(draft: Optional[str], final: Optional[str]) -> float:
    """Token-level edit-diff ratio between a draft and its operator-edited final.

    PINNED (DESIGN.md:252 / HITL_UX.md:58 / DIGEST.md:109 collapse to ONE quantity)::

        edit_diff_ratio = token_levenshtein(tok(draft), tok(final))
                          / max(len(tok(draft)), len(tok(final)))

    Returns a value in ``[0.0, 1.0]``. Two empty/equal texts → ``0.0`` (no edit).
    A review is a "heavy edit" (== a rejection for clean-approval purposes) iff
    this ratio is STRICTLY GREATER than :data:`HEAVY_EDIT_THRESHOLD` (0.30).
    """
    td, tf = _tok(draft), _tok(final)
    denom = max(len(td), len(tf))
    if denom == 0:
        return 0.0
    return _token_levenshtein(td, tf) / denom


def is_heavy_edit(ratio: float) -> bool:
    """True iff ``ratio`` exceeds the 30% heavy-edit threshold (a rejection)."""
    return ratio > HEAVY_EDIT_THRESHOLD


def is_clean_approval(
    decision: str, *, draft_text: Optional[str] = None, final_text: Optional[str] = None
) -> bool:
    """Is this review a CLEAN approval (counts toward clean_approval_rate)?

    A clean approval is an ``approve`` decision, OR an ``edit`` whose token
    edit-diff ratio is ``<= 0.30`` (a light touch-up). A heavy edit (> 0.30), a
    ``reject`` and a ``punt_to_founder`` are NEVER clean. When the decision is
    ``edit`` and no draft/final text is supplied, it is conservatively treated as
    NOT clean (we cannot prove the edit was light).
    """
    if decision == "approve":
        return True
    if decision == "edit":
        if draft_text is None or final_text is None:
            return False
        return not is_heavy_edit(edit_diff_ratio(draft_text, final_text))
    # reject / punt_to_founder / anything else
    return False


def review_is_clean_row(decision: str, edit_diff_size: float) -> bool:
    """Clean-approval verdict from a STORED ``autocm_reviews`` row.

    The 058 ``autocm_reviews`` row persists ``decision`` + the precomputed
    ``edit_diff_size`` (the :func:`edit_diff_ratio` value at review time). This is
    the row-level mirror of :func:`is_clean_approval`: ``approve`` is clean; an
    ``edit`` is clean iff its stored ratio is ``<= 0.30``; reject/punt are not.
    """
    if decision == "approve":
        return True
    if decision == "edit":
        return not is_heavy_edit(edit_diff_size or 0.0)
    return False


# ---------------------------------------------------------------------------
# Promotion gate (DESIGN §7 — HITL → auto)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PromotionVerdict:
    """The outcome of evaluating the DESIGN §7 promotion gate for a category.

    ``promote`` is True iff EVERY condition passed. ``reasons`` enumerates the
    failed conditions (empty when ``promote`` is True) so ``/promote`` (C3.5c) can
    report WHY a category was not promoted. The measured quantities are carried so
    the gate result is auditable.
    """

    promote: bool
    sample_count: int
    clean_approval_rate: float
    hard_refusal_violations: int
    safety_breaches: int
    operator_sign_off: bool
    reasons: List[str] = field(default_factory=list)


def promotion_gate(
    *,
    sample_count: int,
    clean_approval_count: int,
    hard_refusal_violations: int,
    safety_breaches: int,
    operator_sign_off: bool,
) -> PromotionVerdict:
    """Pure DESIGN §7 promotion decision (no I/O).

    ALL of these must hold to promote: ``sample_count >= 50``,
    ``clean_approval_rate >= 0.90`` (clean_approval_count / sample_count), zero
    hard-refusal violations, zero safety breaches, operator sign-off. Each failed
    condition is named in ``reasons``.
    """
    rate = (clean_approval_count / sample_count) if sample_count > 0 else 0.0
    reasons: List[str] = []
    if sample_count < MIN_SAMPLES:
        reasons.append(f"sample_count {sample_count} < {MIN_SAMPLES}")
    if rate < MIN_CLEAN_APPROVAL_RATE:
        reasons.append(
            f"clean_approval_rate {rate:.3f} < {MIN_CLEAN_APPROVAL_RATE}"
        )
    if hard_refusal_violations > 0:
        reasons.append(f"{hard_refusal_violations} hard-refusal violation(s)")
    if safety_breaches > 0:
        reasons.append(f"{safety_breaches} safety breach(es)")
    if not operator_sign_off:
        reasons.append("operator sign-off missing")
    return PromotionVerdict(
        promote=not reasons,
        sample_count=sample_count,
        clean_approval_rate=rate,
        hard_refusal_violations=hard_refusal_violations,
        safety_breaches=safety_breaches,
        operator_sign_off=operator_sign_off,
        reasons=reasons,
    )


@dataclass(frozen=True)
class CategoryReviewStats:
    """Aggregated review stats for one client × category (the gate's inputs)."""

    sample_count: int
    clean_approval_count: int
    hard_refusal_violations: int
    safety_breaches: int


def gather_review_stats(
    conn: Connection,
    client_id: int,
    category: str,
    *,
    since: Optional[str] = None,
) -> CategoryReviewStats:
    """Aggregate the DESIGN §7 promotion inputs from the 058 tables.

    Counts, over ``autocm_reviews`` joined to that client's ``autocm_drafts`` in
    the category (optionally restricted to reviews ``reviewed_at >= since`` for the
    rolling window):

      * ``sample_count``          — reviewed drafts;
      * ``clean_approval_count``  — clean approvals (``is_clean_approval=1`` on the
                                    stored row, which the review write path set via
                                    :func:`review_is_clean_row`);
      * ``hard_refusal_violations`` — reviewed drafts in a hard-refusal category
                                    (price_prediction / financial_advice / legal)
                                    that were NOT clean (a draft that should have
                                    refused but got edited/rejected);
      * ``safety_breaches``       — safety-gate blocks recorded for the category in
                                    the audit log (``action IN ('safety_block',
                                    'injection_blocked')`` — an injection ATTEMPT
                                    counts as a breach for promotion, no exemption).

    The stored ``is_clean_approval`` flag is authoritative for the clean count so
    the gate reads the SAME quantity the review write path computed (no re-derive
    drift); ``since`` bounds the rolling window for the auto-demotion sweep.
    """
    params: dict = {"client_id": client_id, "category": category}
    since_clause = ""
    if since is not None:
        # Defense-in-depth: normalize BOTH sides of the autocm_reviews rolling-window
        # comparison to space form (strip any T/Z) before comparing, mirroring the
        # audit_log breach leg below. The C3.5b write path (record_review_decision)
        # binds reviewed_at explicitly in _iso_z (...T...Z) form so the un-normalized
        # ``r.reviewed_at >= :since`` would already be correct; this normalization
        # additionally protects any row written via the Postgres func.now() COLUMN
        # DEFAULT (space-separated, no T/Z) — which would otherwise sort below a
        # T-prefixed :since and be silently dropped from the window.
        since_clause = (
            " AND REPLACE(REPLACE(r.reviewed_at, 'T', ' '), 'Z', '') >= :since"
        )
        params["since"] = _normalize_audit_ts(since)

    row = conn.execute(
        text(
            "SELECT COUNT(*) AS n, "
            "       COALESCE(SUM(r.is_clean_approval), 0) AS clean "
            "FROM autocm_reviews r "
            "JOIN autocm_drafts d ON d.id = r.draft_id "
            "WHERE r.client_id = :client_id AND d.category = :category"
            f"{since_clause}"
        ),
        params,
    ).fetchone()
    sample_count = int(row[0] or 0)
    clean = int(row[1] or 0)

    # hard-refusal violations: a reviewed draft in a hard-refusal category that was
    # NOT a clean approval (the refusal was edited away / rejected / punted).
    from sable_platform.autocm.classifier.categories import HARD_REFUSAL_CATEGORIES

    hard_refusal_violations = 0
    if category in HARD_REFUSAL_CATEGORIES:
        hard_refusal_violations = sample_count - clean

    # safety breaches recorded for this client × category (audit-log derived).
    # BOTH safety_block AND injection_blocked count: gate/safety writes an injection
    # ATTEMPT under action='injection_blocked' (audit_safety_block / handle_safety_breach
    # when verdict.is_injection), while every other hard-refusal / content-block is
    # action='safety_block'. DESIGN §7 requires ZERO safety-gate breaches for
    # promotion with NO injection exemption — so an injection breach must block
    # promotion exactly like any other safety breach (else a category with a logged
    # injection attempt could still promote).
    breach_params: dict = {"org_id": _org_id_for_client(conn, client_id), "category": category}
    breach_clause = ""
    if since is not None:
        # ``audit_log.timestamp`` is written by the column DEFAULT
        # (018_audit_log.sql ``datetime('now')`` / Alembic ``func.now()``) in the
        # SPACE-separated ``YYYY-MM-DD HH:MM:SS`` form, NOT the ``...T...Z`` form
        # ``since`` carries (and the autocm convention binds elsewhere). The two
        # forms are not lexically comparable (space 0x20 < 'T' 0x54), so a naive
        # ``timestamp >= :since`` would silently drop a same-window breach row.
        # Normalize BOTH sides to the space-separated form before comparing: strip
        # any T/Z from the stored value and bind ``since`` in space form. This makes
        # the windowed breach count correct regardless of which form the audit row
        # was written in (default OR an explicit _iso_z bind).
        breach_clause = (
            " AND REPLACE(REPLACE(timestamp, 'T', ' '), 'Z', '') >= :breach_since"
        )
        breach_params["breach_since"] = _normalize_audit_ts(since)
    breach_row = conn.execute(
        text(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE action IN ('safety_block', 'injection_blocked') AND org_id = :org_id "
            "  AND detail_json LIKE :cat_like"
            f"{breach_clause}"
        ),
        {**breach_params, "cat_like": f'%"category": "{category}"%'},
    ).fetchone()
    safety_breaches = int(breach_row[0] or 0)

    return CategoryReviewStats(
        sample_count=sample_count,
        clean_approval_count=clean,
        hard_refusal_violations=hard_refusal_violations,
        safety_breaches=safety_breaches,
    )


def _org_id_for_client(conn: Connection, client_id: int) -> Optional[str]:
    row = conn.execute(
        text("SELECT org_id FROM autocm_clients WHERE id = :id"),
        {"id": client_id},
    ).fetchone()
    return row[0] if row is not None else None


def promote_category(
    conn: Connection,
    client_id: int,
    category: str,
    *,
    actor: str,
    operator_sign_off: bool,
    org_id: Optional[str] = None,
) -> PromotionVerdict:
    """Run the DESIGN §7 gate and flip the category to ``auto`` iff it passes.

    Gathers the review stats, evaluates :func:`promotion_gate`, and — ONLY when the
    verdict is ``promote`` — updates the ``autocm_category_state`` row to
    ``state='auto'`` and writes an ``autonomy_promoted`` audit row. Returns the
    verdict either way (``/promote`` reports it).

    A never-auto category (registry ``auto_eligible=False``: tier-3 + the never-auto
    tier-2 routing categories — incident / threat / conflict_detected /
    moderation_flag / whale_inbound / founder_voice_needed) is SHORT-CIRCUITED
    before any write: it returns a non-promoting verdict with reason
    ``'category is never-auto'`` and performs NO state flip and NO audit row. This
    is a real guard, not merely a read-time backstop — without it a never-auto
    category that accrued clean reviews + operator sign-off would get its persisted
    state flipped to ``'auto'`` (a lie — none of these are in
    HARD_REFUSAL_CATEGORIES, so the gate raises no hard-refusal violation for them)
    and an ``autonomy_promoted`` row logged, contradicting SAFETY §5's
    authoritative-audit guarantee. (The registry-level ``auto_eligible`` remains the
    final word at read time in ``gate/confidence`` so a stray ``'auto'`` row could
    never actually auto-send, but the persisted state + audit trail must not lie.)

    A category currently under a SAFETY §6 freeze (active ``freeze_until``) is also
    refused: it returns a non-promoting verdict (reason ``'category frozen …'``) and
    does NOT bank a promotion through the freeze window — a frozen category must
    re-pass the gate AFTER the freeze elapses, never silently re-arm the instant it
    lifts.
    """
    # never-auto guard (DESIGN §7 / §5): refuse + audit-nothing for a category the
    # registry says can NEVER be autonomous, before any state write or audit row.
    # An UNKNOWN category (not in the registry — get_category_def is None) is treated
    # the SAME as never-auto: a hallucinated category must never have its persisted
    # state flipped to 'auto' or an autonomy_promoted row written (the read-side
    # decide()/resolve_category_state already returns None → HITL for unknown
    # categories, so a stray 'auto' row could never auto-send — but the persisted
    # state + audit trail must not lie, the exact invariant this guard protects).
    from sable_platform.autocm.classifier.categories import get_category_def

    cdef = get_category_def(category)
    if cdef is None:
        return PromotionVerdict(
            promote=False,
            sample_count=0,
            clean_approval_rate=0.0,
            hard_refusal_violations=0,
            safety_breaches=0,
            operator_sign_off=operator_sign_off,
            reasons=["unknown_category"],
        )
    if not cdef.auto_eligible:
        return PromotionVerdict(
            promote=False,
            sample_count=0,
            clean_approval_rate=0.0,
            hard_refusal_violations=0,
            safety_breaches=0,
            operator_sign_off=operator_sign_off,
            reasons=["category is never-auto"],
        )

    # SAFETY §6 freeze guard: do not promote (and do not bank a promotion through)
    # a category whose pure-HITL freeze window is still active.
    from sable_platform.autocm.gate.confidence import is_frozen

    if is_frozen(conn, client_id, category):
        return PromotionVerdict(
            promote=False,
            sample_count=0,
            clean_approval_rate=0.0,
            hard_refusal_violations=0,
            safety_breaches=0,
            operator_sign_off=operator_sign_off,
            reasons=["category frozen under SAFETY §6 freeze_until"],
        )

    stats = gather_review_stats(conn, client_id, category)
    verdict = promotion_gate(
        sample_count=stats.sample_count,
        clean_approval_count=stats.clean_approval_count,
        hard_refusal_violations=stats.hard_refusal_violations,
        safety_breaches=stats.safety_breaches,
        operator_sign_off=operator_sign_off,
    )
    if not verdict.promote:
        return verdict

    if org_id is None:
        org_id = _org_id_for_client(conn, client_id)
    _set_category_state(conn, client_id, category, "auto")
    log_audit(
        conn,
        actor=actor,
        action=ACTION_PROMOTE,
        org_id=org_id,
        entity_id=f"{client_id}:{category}",
        detail={
            "client_id": client_id,
            "category": category,
            "sample_count": verdict.sample_count,
            "clean_approval_rate": round(verdict.clean_approval_rate, 4),
            "operator_sign_off": operator_sign_off,
        },
        source=AUDIT_SOURCE,
    )
    return verdict


# ---------------------------------------------------------------------------
# Auto-demotion — trigger (3): synchronous safety-slip
# ---------------------------------------------------------------------------
def demote_on_safety_slip(
    conn: Connection,
    client_id: int,
    category: str,
    *,
    actor: str = "sable-autocm",
    org_id: Optional[str] = None,
    detail: Optional[dict] = None,
) -> bool:
    """DESIGN §7 trigger (3): a safety-gate violation slipped through on ``auto``.

    Immediately flips the category to ``hitl`` and writes an
    ``autonomy_demoted_safety_slip`` audit row. Returns True iff the category was
    actually ``auto`` and got flipped (idempotent — a category already HITL is a
    no-op that returns False). Safety-relevant and synchronous: this is wired from
    ``gate/safety`` so any safety breach on an autonomous category demotes it on
    the spot, before the next draft.
    """
    current = _get_category_state(conn, client_id, category)
    if current != "auto":
        return False
    if org_id is None:
        org_id = _org_id_for_client(conn, client_id)
    _set_category_state(conn, client_id, category, "hitl")
    log_audit(
        conn,
        actor=actor,
        action=ACTION_DEMOTE_SAFETY,
        org_id=org_id,
        entity_id=f"{client_id}:{category}",
        detail={"client_id": client_id, "category": category, **(detail or {})},
        source=AUDIT_SOURCE,
    )
    return True


# ---------------------------------------------------------------------------
# Auto-demotion — trigger (1): rolling-7d sweep (the scheduled job)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DemotionOutcome:
    """One category demoted by the rolling-7d sweep."""

    client_id: int
    category: str
    sample_count: int
    clean_approval_rate: float


def sweep_auto_demotions(
    conn: Connection,
    client_id: int,
    *,
    now: Optional[datetime] = None,
    actor: str = "sable-autocm",
    org_id: Optional[str] = None,
    min_samples: int = 1,
) -> List[DemotionOutcome]:
    """DESIGN §7 trigger (1): rolling-7d clean-approval ``< 0.85`` → HITL.

    For every category currently in ``state='auto'`` for the client, compute the
    rolling-7d clean-approval rate (reviews in the last :data:`ROLLING_WINDOW_DAYS`
    days). If the rate is below :data:`AUTO_DEMOTE_RATE` (0.85), flip the category
    back to ``hitl`` (no operator action) and write an
    ``autonomy_auto_demoted_rolling7d`` audit row. Returns the demoted categories.

    A category with FEWER than ``min_samples`` reviews in the window is left alone
    (too little signal to demote on); ``min_samples`` defaults to 1 so any negative
    7d window demotes, but a sweep can require more samples to act. The clock is
    injectable so the rolling window is deterministic under test.
    """
    now = now or _utc_now()
    since = _iso_z(now - timedelta(days=ROLLING_WINDOW_DAYS))
    if org_id is None:
        org_id = _org_id_for_client(conn, client_id)

    auto_rows = conn.execute(
        text(
            "SELECT category FROM autocm_category_state "
            "WHERE client_id = :client_id AND state = 'auto' "
            "ORDER BY category"
        ),
        {"client_id": client_id},
    ).fetchall()

    demoted: List[DemotionOutcome] = []
    for r in auto_rows:
        category = r[0]
        stats = gather_review_stats(conn, client_id, category, since=since)
        if stats.sample_count < min_samples:
            continue
        rate = stats.clean_approval_count / stats.sample_count
        if rate >= AUTO_DEMOTE_RATE:
            continue
        _set_category_state(conn, client_id, category, "hitl")
        log_audit(
            conn,
            actor=actor,
            action=ACTION_DEMOTE_ROLLING,
            org_id=org_id,
            entity_id=f"{client_id}:{category}",
            detail={
                "client_id": client_id,
                "category": category,
                "window_days": ROLLING_WINDOW_DAYS,
                "sample_count": stats.sample_count,
                "clean_approval_rate": round(rate, 4),
                "threshold": AUTO_DEMOTE_RATE,
            },
            source=AUDIT_SOURCE,
        )
        demoted.append(
            DemotionOutcome(
                client_id=client_id,
                category=category,
                sample_count=stats.sample_count,
                clean_approval_rate=rate,
            )
        )
    return demoted


# ---------------------------------------------------------------------------
# autocm_category_state row helpers (upsert-on-write; HITL-by-default floor)
# ---------------------------------------------------------------------------
def _get_category_state(conn: Connection, client_id: int, category: str) -> Optional[str]:
    row = conn.execute(
        text(
            "SELECT state FROM autocm_category_state "
            "WHERE client_id = :c AND category = :cat"
        ),
        {"c": client_id, "cat": category},
    ).fetchone()
    return row[0] if row is not None else None


def _set_category_state(
    conn: Connection, client_id: int, category: str, state: str
) -> None:
    """Idempotent upsert of the runtime ``state`` for a client × category.

    A category with no row yet is HITL-by-default (the 058 column default); this
    upsert creates the row on first transition and bumps ``updated_at``. Uses the
    058 ``autocm_category_state_unique (client_id, category)`` index for the
    conflict target.
    """
    conn.execute(
        text(
            "INSERT INTO autocm_category_state (client_id, category, state, updated_at) "
            "VALUES (:c, :cat, :state, :now) "
            "ON CONFLICT (client_id, category) DO UPDATE SET "
            "  state = excluded.state, updated_at = excluded.updated_at"
        ),
        {"c": client_id, "cat": category, "state": state, "now": _iso_z(_utc_now())},
    )


__all__ = [
    # thresholds
    "MIN_SAMPLES",
    "MIN_CLEAN_APPROVAL_RATE",
    "HEAVY_EDIT_THRESHOLD",
    "AUTO_DEMOTE_RATE",
    "ROLLING_WINDOW_DAYS",
    # edit-diff quantity
    "edit_diff_ratio",
    "is_heavy_edit",
    "is_clean_approval",
    "review_is_clean_row",
    # promotion
    "PromotionVerdict",
    "promotion_gate",
    "promote_category",
    "CategoryReviewStats",
    "gather_review_stats",
    # demotion
    "demote_on_safety_slip",
    "sweep_auto_demotions",
    "DemotionOutcome",
]
