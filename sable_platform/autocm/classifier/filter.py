"""Heuristic-first engagement filter (DESIGN §4 ``classifier/filter``) — D-1 reuse.

This is the AutoCM-native engagement filter wired over the VENDORED
``sable_pulse_core.engagement`` engine (the D-1 reuse, C3.1). The vendored
``assess`` is the TEXT-ONLY engage/skip/ambiguous heuristic — pure, deterministic,
no runtime state, ~70% of TG traffic eliminated before any LLM (DESIGN §4).

The STATEFUL strong-skips (auto-silenced ``autocm_flagged_users``, recent-reply
throttling, founder pre-emption) are AutoCM-only and land in C3.4a; this module is
the deterministic text leg + the seam where C3.4a will layer the stateful gate.
``FilterDecision`` is the constant set so call sites don't string-compare raw.
"""
from __future__ import annotations

from typing import Optional

# D-1 reuse: the vendored deterministic engagement engine (NOT the sibling repo).
from sable_platform._vendor.sable_pulse_core import EngagementResult, assess


class FilterDecision:
    """The engage/skip/ambiguous decision constants (mirrors the vendored engine)."""

    ENGAGE = "engage"
    SKIP = "skip"
    AMBIGUOUS = "ambiguous"

    ALL = (ENGAGE, SKIP, AMBIGUOUS)


def assess_engagement(
    text: str,
    *,
    is_reply_to_bot: bool,
    is_mention: bool,
    bot_username: Optional[str],
) -> EngagementResult:
    """Run the deterministic text-only engagement heuristic (vendored ``assess``).

    Returns the vendored :class:`EngagementResult` (``decision`` ∈
    :data:`FilterDecision.ALL`). C3.4a wraps this with the stateful strong-skips;
    C3.1 wires the deterministic leg so the reuse is real, not a stub.
    """
    return assess(
        text,
        is_reply_to_bot=is_reply_to_bot,
        is_mention=is_mention,
        bot_username=bot_username,
    )


__all__ = ["EngagementResult", "FilterDecision", "assess_engagement"]
