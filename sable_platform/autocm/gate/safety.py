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

# D-1 reuse: the vendored deterministic safety bank (NOT the sibling repo).
from sable_platform._vendor.sable_pulse_core import RefusalMatch, check_refusal


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


def check_safety(text: str) -> SafetyVerdict:
    """Run the vendored hard-refusal + content-block detector over ``text``.

    Returns a :class:`SafetyVerdict`; ``tripped=False`` means clean. Deterministic,
    offline. The full per-category escalation/flagged-user wiring is C3.5a/C3.8a.
    """
    match = check_refusal(text or "")
    return SafetyVerdict(tripped=match is not None, match=match)


__all__ = ["SafetyVerdict", "check_safety", "RefusalMatch"]
