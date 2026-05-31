"""Safety gate (DESIGN §4 ``gate/safety``) — D-1 reuse.

The AutoCM-native safety gate wired over the VENDORED
``sable_pulse_core.safety`` hard-refusal + content-block detector (the D-1
reuse, C3.1). The vendored bank is asserted a SUPERSET of SAFETY.md §1 (six
hard-refusal categories) + §3 (six content blocks) by the C3.1 vendor-drift /
safety-superset test, so this gate cannot regress coverage between syncs.

The gate is the safety-FIRST stage of the pipeline: a fired refusal forces the
reactive register (SAFETY §0) and means the bot NEVER auto-answers in voice — it
refuses (calibrated reactive NULO wording) or escalates per the category tier.
C3.1 wires the deterministic detector; the per-category escalation routing +
flagged-user state are C3.5a / C3.8a.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.engine import Connection

# D-1 reuse: the vendored deterministic safety bank (NOT the sibling repo).
from sable_platform._vendor.sable_pulse_core import RefusalMatch, check_refusal
from sable_platform.autocm.gate.autonomy import demote_on_safety_slip
from sable_platform.db.audit import log_audit

# log_audit verbs (SAFETY §2/§5/§6 blocked-path audit). source="sable-autocm".
AUDIT_SOURCE = "sable-autocm"
ACTION_SAFETY_BLOCK = "safety_block"
ACTION_INJECTION_BLOCKED = "injection_blocked"

# The HITL-queue flag surfaced on a failed injection scan (SAFETY §2: block +
# escalate). The C3.5b review queue reads this flag to mark the item.
INJECTION_ATTEMPT_FLAG = "INJECTION_ATTEMPT"

# The vendored prompt-injection category — a fired match in this category is an
# injection ATTEMPT (audited as injection_blocked + flagged), distinct from the
# other hard-refusals/content-blocks (audited as safety_block).
INJECTION_CATEGORY = "prompt_injection"


@dataclass(frozen=True)
class SafetyVerdict:
    """The safety gate's decision for a piece of text.

    ``tripped`` is True iff the vendored bank fired; ``match`` carries the fired
    :class:`RefusalMatch` (category / kind / trigger / register). When tripped, the
    register is always 'reactive' (SAFETY §0) and the bot must NOT auto-answer.
    """

    tripped: bool
    match: Optional[RefusalMatch] = None

    @property
    def category(self) -> Optional[str]:
        return self.match.category if self.match else None

    @property
    def kind(self) -> Optional[str]:
        return self.match.kind if self.match else None

    @property
    def is_injection(self) -> bool:
        """True iff the fired match is a prompt-injection ATTEMPT (SAFETY §2)."""
        return self.tripped and self.category == INJECTION_CATEGORY

    @property
    def trigger(self) -> Optional[str]:
        return self.match.trigger if self.match else None


def check_safety(text: str) -> SafetyVerdict:
    """Run the vendored hard-refusal + content-block detector over ``text``.

    Returns a :class:`SafetyVerdict`; ``tripped=False`` means clean. Deterministic,
    offline. The full per-category escalation/flagged-user wiring is C3.5a/C3.8a.
    """
    match = check_refusal(text or "")
    return SafetyVerdict(tripped=match is not None, match=match)


# ---------------------------------------------------------------------------
# Blocked-path audit (SAFETY §2/§5/§6) — the NOT-published path
# ---------------------------------------------------------------------------
def audit_safety_block(
    conn: Connection,
    verdict: SafetyVerdict,
    *,
    org_id: Optional[str],
    category: Optional[str] = None,
    source_message_id: Optional[int] = None,
    actor: str = "sable-autocm",
) -> Optional[int]:
    """Write the SAFETY §5 blocked-path audit row for a fired safety gate.

    Every safety-gate BLOCK writes an audit row even though nothing is published
    (so SAFETY §5's "did the bot ever encounter X" is complete for the most
    security-relevant events). A fired prompt-injection match is audited as
    ``injection_blocked`` (SAFETY §2 block + escalate); any other fired
    hard-refusal / content-block is audited as ``safety_block``. A clean verdict
    (``tripped=False``) writes nothing and returns ``None``.

    Returns the audit row id (or ``None`` when nothing fired).
    """
    if not verdict.tripped:
        return None
    action = ACTION_INJECTION_BLOCKED if verdict.is_injection else ACTION_SAFETY_BLOCK
    return log_audit(
        conn,
        actor=actor,
        action=action,
        org_id=org_id,
        detail={
            "category": category if category is not None else verdict.category,
            "safety_category": verdict.category,
            "kind": verdict.kind,
            "pattern": verdict.trigger,
            "source_message_id": source_message_id,
        },
        source=AUDIT_SOURCE,
    )


def audit_injection_blocked(
    conn: Connection,
    trigger: Optional[str],
    *,
    org_id: Optional[str],
    category: Optional[str] = None,
    source_message_id: Optional[int] = None,
    actor: str = "sable-autocm",
) -> int:
    """Audit a failed injection scan detected OUTSIDE the vendored gate.

    The classifier-stage early-detect (C3.4a) and the late gate both surface
    injection attempts; this records the ``injection_blocked`` row + carries the
    fired ``pattern`` so the HITL queue can show the :data:`INJECTION_ATTEMPT_FLAG`
    (SAFETY §2: block + escalate). Use :func:`audit_safety_block` when you already
    hold a :class:`SafetyVerdict`; use this when you only have the fired trigger.
    """
    return log_audit(
        conn,
        actor=actor,
        action=ACTION_INJECTION_BLOCKED,
        org_id=org_id,
        detail={
            "category": category,
            "pattern": trigger,
            "source_message_id": source_message_id,
        },
        source=AUDIT_SOURCE,
    )


# ---------------------------------------------------------------------------
# DESIGN §7 trigger (3): safety-gate violation slips through on an auto category
# ---------------------------------------------------------------------------
def handle_safety_breach(
    conn: Connection,
    verdict: SafetyVerdict,
    *,
    client_id: int,
    category: str,
    org_id: Optional[str] = None,
    source_message_id: Optional[int] = None,
    actor: str = "sable-autocm",
) -> tuple[Optional[int], bool]:
    """Full blocked-path handling for a safety breach on a (possibly auto) category.

    Wires the SAFETY §5 blocked-path audit AND the DESIGN §7 trigger-(3)
    demote-on-slip in one call: writes the ``safety_block`` / ``injection_blocked``
    audit row, then — if the category was autonomous (``state='auto'``) — flips it
    back to ``hitl`` immediately (a safety-gate violation on an auto category must
    not persist for the next draft) and writes its own demotion audit row.

    Returns ``(audit_row_id, demoted)`` — ``demoted`` True iff the category was
    actually flipped from ``auto`` to ``hitl``. A clean verdict is a no-op
    returning ``(None, False)``.
    """
    if not verdict.tripped:
        return None, False
    audit_id = audit_safety_block(
        conn,
        verdict,
        org_id=org_id,
        category=category,
        source_message_id=source_message_id,
        actor=actor,
    )
    demoted = demote_on_safety_slip(
        conn,
        client_id,
        category,
        actor=actor,
        org_id=org_id,
        detail={
            "safety_category": verdict.category,
            "kind": verdict.kind,
            "pattern": verdict.trigger,
            "source_message_id": source_message_id,
        },
    )
    return audit_id, demoted


__all__ = [
    "SafetyVerdict",
    "check_safety",
    "RefusalMatch",
    "INJECTION_ATTEMPT_FLAG",
    "INJECTION_CATEGORY",
    "ACTION_SAFETY_BLOCK",
    "ACTION_INJECTION_BLOCKED",
    "audit_safety_block",
    "audit_injection_blocked",
    "handle_safety_breach",
]
