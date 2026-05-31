"""Register selection — calm vs reactive (DESIGN §4 / CLASSIFIER §2.5).

(MEGAPLAN C3.4b — the bimodal-NULO register chooser: calm Bill-Monday default vs
reactive HK-47 charged register.)

Two layers, both deterministic and zero-LLM at this seam:

  (i)  :func:`select_register` — the low-level primitive (kept from the C3.1
       skeleton, consumed by ``drafter/persona``): a hard refusal forces reactive
       (SAFETY §0); otherwise calm. Stable signature so existing callers do not
       break.

  (ii) :func:`choose_register` — the C3.4b selection used by the tier classifier.
       It folds together three inputs in priority order (CLASSIFIER §2.5 / VOICE.md
       §1, §5):

         1. **hard refusal / safety charge** (``is_refusal``) → ALWAYS reactive
            (SAFETY §0 — refusals are charged content by definition; never
            overridden).
         2. **the category's registry default register** (CLASSIFIER §2 "register
            default" column) — e.g. ``FUD_borderline`` defaults reactive, ``price``
            defaults calm. A ``None`` registry register (a category that never
            produces a public reply) yields calm here only as an inert default — the
            caller suppresses the reply, so the register is moot.
         3. **a detected charge signal in the message** (hostility / manipulation /
            energetic FUD / skeptical-follow-up) — bumps an otherwise-calm category
            to reactive (the CLASSIFIER §2.5 "reactive triggers" / VOICE §5 charged
            moments).

       When NONE of those fire, the floor is **calm** (CLASSIFIER §2.5: "If register
       is hard to determine, default to calm. Calm is the safer floor … operator HITL
       catches" a too-soft calm on a charged question; a robotic register on a neutral
       question feels wrong).

The charge detector (iii) is a CONSERVATIVE deterministic heuristic — it only
flips to reactive on a CLEAR charge signal (per CLASSIFIER §2.5 / §3: "Reactive
register signals charge — only use when charge is clear"). It is intentionally
narrow; the LLM tier classifier may additionally emit a register, and the C3.4b
call uses :func:`choose_register` as the deterministic floor/override around it.

No telegram / anthropic import; pure Python regex + the category registry default.
"""
from __future__ import annotations

import re
from typing import Optional

CALM = "calm"
REACTIVE = "reactive"
REGISTERS = (CALM, REACTIVE)


def select_register(text: str, *, is_refusal: bool = False) -> str:
    """Low-level register primitive (C3.1 contract; consumed by drafter/persona).

    A hard refusal is ALWAYS reactive (SAFETY §0). Otherwise this is the calm
    floor. The nuanced category-aware + charge-aware selection is
    :func:`choose_register` (C3.4b) — this primitive stays stable for the drafter.
    """
    if is_refusal:
        return REACTIVE
    return CALM


# ---------------------------------------------------------------------------
# Charge detection (CLASSIFIER §2.5 reactive triggers / VOICE §5 charged moments).
# Conservative: only CLEAR charge flips an otherwise-calm category to reactive.
# ---------------------------------------------------------------------------
_FLAGS = re.IGNORECASE

# Reactive triggers (CLASSIFIER §2.5): hostility / dismissive tone / insult markers;
# manipulation ("ignore previous instructions" / "system prompt" — also a safety
# trigger, but caught here too as a charge signal); energetic FUD ("dead", "rugged",
# "scam", "wtf"); skeptical / "are you a bot" with attitude. Each pattern is anchored
# with word boundaries to avoid firing on substrings (e.g. "scamper", "deadline").
_CHARGE_PATTERNS: tuple[re.Pattern, ...] = (
    # hostility / insult / dismissiveness
    re.compile(r"\b(stfu|shut\s+up|idiot|moron|stupid|clown|trash|garbage|useless)\b", _FLAGS),
    re.compile(r"\b(wtf|wth|gtfo|lmfao)\b", _FLAGS),
    re.compile(r"\byou\s+(suck|lie|lied|lying)\b", _FLAGS),
    # energetic FUD (charged by definition — VOICE: "FUD with energy")
    re.compile(r"\b(rug|rugged|rugging|rugpull|rug\s+pull)\b", _FLAGS),
    re.compile(r"\b(scam|scammer|ponzi|exit\s+scam)\b", _FLAGS),
    re.compile(r"\b(dead|dying|dump(ing|ed)?|tanking|over)\b.*\b(project|coin|token|this)\b", _FLAGS),
    re.compile(r"\bthis\s+(thing|project|coin|token)\s+is\s+(dead|dying|done|over|finished)\b", _FLAGS),
    # manipulation / injection (also a hard-refusal, but a clear charge signal)
    re.compile(r"\bignore\s+(all\s+)?(previous|prior|above|the)\s+(instructions?|prompts?|rules?)\b", _FLAGS),
    re.compile(r"\b(system\s+prompt|your\s+config(uration)?)\b", _FLAGS),
    re.compile(r"\byou\s+are\s+now\b", _FLAGS),
    re.compile(r"\bpretend\s+(you'?re|you\s+are|to\s+be)\b", _FLAGS),
    # skeptical / accusatory framing ("are you even real", "is this a bot lol")
    re.compile(r"\bare\s+you\s+(even\s+)?(a\s+)?(real|human|bot|fake)\b", _FLAGS),
)


def detect_charge(text: Optional[str]) -> bool:
    """True iff ``text`` carries a CLEAR charge signal (reactive trigger).

    Conservative by design (CLASSIFIER §2.5 / §3): a neutral factual question must
    NOT trip this — calm is the safer floor and a false-positive reactive on a
    neutral question reads wrong. Only the explicit hostility / manipulation /
    energetic-FUD / skeptical markers above flip it. Empty / None → no charge.
    """
    body = text or ""
    if not body:
        return False
    return any(p.search(body) for p in _CHARGE_PATTERNS)


def choose_register(
    *,
    is_refusal: bool = False,
    category_default: Optional[str] = None,
    message: Optional[str] = None,
    llm_register: Optional[str] = None,
) -> str:
    """Pick the bimodal register for a classified message (C3.4b).

    Priority (CLASSIFIER §2.5 / VOICE §1):

      1. ``is_refusal`` → ALWAYS reactive (SAFETY §0, never overridden).
      2. the category's registry default register (``category_default``) when it is
         ``reactive`` — a category whose §2 default is reactive (FUD_borderline,
         hard refusals, incident) stays reactive.
      3. a CLEAR charge signal in ``message`` (:func:`detect_charge`) → reactive,
         bumping an otherwise-calm category (CLASSIFIER §4 mixed-register rule:
         "factual content + hostile tone → register goes reactive").
      4. a valid ``llm_register`` of ``reactive`` (the LLM tier call may emit a
         register; it can only ESCALATE calm→reactive, never soften a charge to
         calm — calm is the floor and a confident-wrong calm on a charged message
         is the worse error, caught by HITL).

    Otherwise the floor is **calm** (the default; CLASSIFIER §2.5). An invalid
    ``category_default`` / ``llm_register`` (anything not in :data:`REGISTERS`) is
    ignored, never raised — a bad value must not break routing, and calm is the safe
    fallback.
    """
    if is_refusal:
        return REACTIVE
    if category_default == REACTIVE:
        return REACTIVE
    if detect_charge(message):
        return REACTIVE
    if llm_register == REACTIVE:
        return REACTIVE
    return CALM


__all__ = [
    "CALM",
    "REACTIVE",
    "REGISTERS",
    "select_register",
    "detect_charge",
    "choose_register",
]
