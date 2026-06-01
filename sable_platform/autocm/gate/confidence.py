"""Confidence + autonomy gate (DESIGN §4 ``gate/confidence``) — C3.5a.

The decision stage: given a draft's confidence + its category, decide AUTO vs
HITL for THIS client × category. Reads the merged per-client × per-category view
from ``autocm_category_state`` (via
:func:`~sable_platform.autocm.classifier.categories.resolve_category_state`) and
applies the autonomy rules:

  1. **never-auto** — a tier-3 / conflict_detected / moderation_flag / incident
     category (registry ``auto_eligible=False``) is ALWAYS HITL, no matter the
     runtime row or confidence (the registry invariant is the final word — a stray
     ``state='auto'`` row can never make it autonomous);
  2. **freeze_until** — while the client × category row carries an active
     ``freeze_until`` (the SAFETY §6 48h pure-HITL freeze, owned by C3.8a / set in
     058), the category is forced to HITL even if its state is ``auto``; the bot
     keeps drafting + HITL-reviewing, only autonomous auto-send is frozen;
  3. **runtime state** — a category whose runtime ``state`` is still ``hitl`` (the
     safe default for every fresh client) is HITL;
  4. **confidence floor** — even an auto-eligible, ``auto``-state, unfrozen
     category only auto-sends when the draft's confidence meets the per-category
     threshold (the runtime override when present, else the registry floor).

ALL four must clear for AUTO; otherwise HITL. The gate is the read-side counterpart
of the ``gate/autonomy`` state machine (which WRITES the ``state`` column on
promotion/demotion); this module only READS. It embeds no LLM / network — the
``confidence`` value is produced upstream by the C3.4b tier classifier / drafter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.autocm.classifier.categories import resolve_category_state

# Gate outcomes.
AUTO = "auto"
HITL = "hitl"

# Reason codes (stable strings — the HITL queue / audit can branch on them).
REASON_AUTO = "auto"
REASON_UNKNOWN_CATEGORY = "unknown_category"
REASON_NEVER_AUTO = "never_auto"
REASON_FROZEN = "frozen"
REASON_HITL_STATE = "hitl_state"
REASON_BELOW_THRESHOLD = "below_threshold"


@dataclass(frozen=True)
class ConfidenceVerdict:
    """The decision-gate result for one draft.

    ``outcome`` is :data:`AUTO` or :data:`HITL`; ``reason`` is one of the stable
    ``REASON_*`` codes (the HITL queue / audit branch on it). The measured
    quantities (confidence, threshold, state, freeze) are carried so the decision
    is auditable / explainable.
    """

    outcome: str  # 'auto' | 'hitl'
    reason: str
    confidence: float
    threshold: Optional[float]
    state: str  # 'hitl' | 'auto' (merged runtime state, never-auto forced 'hitl')
    auto_eligible: bool
    frozen: bool

    @property
    def is_auto(self) -> bool:
        return self.outcome == AUTO


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse a stored ``...Z`` / ``+00:00`` / naive timestamp → tz-aware UTC."""
    if not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_frozen(
    conn: Connection,
    client_id: int,
    category: str,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """True iff this client × category has an ACTIVE SAFETY §6 freeze.

    Reads ``autocm_category_state.freeze_until``; a row whose ``freeze_until`` is
    in the future (relative to ``now``, UTC) is frozen. A NULL / past
    ``freeze_until`` (and a category with no row) is not frozen. The freeze is set
    by C3.8a's 48h pure-HITL mode; this gate only READS it. ``now`` is injectable
    so the freeze window is deterministic under test.
    """
    now = now or datetime.now(timezone.utc)
    row = conn.execute(
        text(
            "SELECT freeze_until FROM autocm_category_state "
            "WHERE client_id = :c AND category = :cat"
        ),
        {"c": client_id, "cat": category},
    ).fetchone()
    if row is None:
        return False
    until = _parse_iso(row[0])
    return until is not None and until > now


def decide(
    conn: Connection,
    client_id: int,
    category: str,
    confidence: float,
    *,
    now: Optional[datetime] = None,
) -> ConfidenceVerdict:
    """Decide AUTO vs HITL for a draft (the C3.5a decision gate).

    Resolves the merged registry+runtime category view, then applies, IN ORDER:
    unknown-category (→ HITL, the §6 hallucination guard), never-auto (→ HITL),
    active freeze (→ HITL), runtime ``hitl`` state (→ HITL), and finally the
    confidence floor (→ HITL when ``confidence < threshold``). Only a known,
    auto-eligible, ``auto``-state, unfrozen category whose confidence clears the
    threshold returns :data:`AUTO`.
    """
    now = now or datetime.now(timezone.utc)
    merged = resolve_category_state(conn, client_id, category)

    # (0) unknown category — the §6 hallucination guard defaults to HITL.
    if merged is None:
        return ConfidenceVerdict(
            outcome=HITL,
            reason=REASON_UNKNOWN_CATEGORY,
            confidence=confidence,
            threshold=None,
            state=HITL,
            auto_eligible=False,
            frozen=False,
        )

    frozen = is_frozen(conn, client_id, category, now=now)

    # (1) never-auto registry categories are ALWAYS HITL (registry is final word).
    if not merged.auto_eligible:
        return ConfidenceVerdict(
            outcome=HITL,
            reason=REASON_NEVER_AUTO,
            confidence=confidence,
            threshold=merged.confidence_threshold,
            state=merged.state,
            auto_eligible=False,
            frozen=frozen,
        )

    # (2) active SAFETY §6 freeze forces HITL even on an `auto`-state category.
    if frozen:
        return ConfidenceVerdict(
            outcome=HITL,
            reason=REASON_FROZEN,
            confidence=confidence,
            threshold=merged.confidence_threshold,
            state=merged.state,
            auto_eligible=True,
            frozen=True,
        )

    # (3) runtime state still HITL (the safe default until the state machine promotes).
    if not merged.is_auto:
        return ConfidenceVerdict(
            outcome=HITL,
            reason=REASON_HITL_STATE,
            confidence=confidence,
            threshold=merged.confidence_threshold,
            state=merged.state,
            auto_eligible=True,
            frozen=False,
        )

    # (4) confidence floor — an `auto` category still HITLs a low-confidence draft.
    threshold = merged.confidence_threshold
    if threshold is not None and confidence < threshold:
        return ConfidenceVerdict(
            outcome=HITL,
            reason=REASON_BELOW_THRESHOLD,
            confidence=confidence,
            threshold=threshold,
            state=merged.state,
            auto_eligible=True,
            frozen=False,
        )

    return ConfidenceVerdict(
        outcome=AUTO,
        reason=REASON_AUTO,
        confidence=confidence,
        threshold=threshold,
        state=merged.state,
        auto_eligible=True,
        frozen=False,
    )


__all__ = [
    "AUTO",
    "HITL",
    "REASON_AUTO",
    "REASON_UNKNOWN_CATEGORY",
    "REASON_NEVER_AUTO",
    "REASON_FROZEN",
    "REASON_HITL_STATE",
    "REASON_BELOW_THRESHOLD",
    "ConfidenceVerdict",
    "is_frozen",
    "decide",
]
