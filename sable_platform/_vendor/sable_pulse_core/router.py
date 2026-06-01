"""Pure deterministic category classifier.

`classify(text)` maps a piece of user text to ONE coarse category that decides how
the CM pipeline routes it downstream:

    greeting   — gm / hello / new-arrival social glue → calm welcome
    glossary   — "what is X" definitional asks → calm glossary wrap
    slotfill    — an irreducible-fact lookup (contract / vault / audit / handle) → calm slot-fill
    price      — price-prediction / "wen moon" shaped asks → reactive refusal (charged)
    refusal    — other charged hard-refusal / content-block shapes → reactive refusal
    status     — vault / TVL / buyback "what's the current X" factual reads
    unknown    — everything else → escalate (the deterministic surface can't auto-answer)

This module is intentionally dependency-free: it does NOT import `kb` or `safety`
(MEGAPLAN C0.1 "router.py — pure deterministic category classifier … No deps on
kb/safety"). The richer safety taxonomy lives in `safety.py`; the router only needs
the coarse routing label. `price`/`refusal` here are *routing hints* — the safety
gate (`safety.check_refusal`) is the authoritative refusal detector and runs
alongside; the cm pipeline reconciles them.

Deterministic: same text → same category, every time. No network, no LLM.
"""
from __future__ import annotations

import re

CATEGORIES = (
    "greeting",
    "glossary",
    "slotfill",
    "price",
    "refusal",
    "status",
    "unknown",
)

_FLAGS = re.IGNORECASE

# Order of evaluation is deliberate (most-charged / most-specific first), so a
# message that looks like several things resolves to the highest-stakes routing.

# price-prediction shapes → reactive refusal (kept distinct from generic refusal so
# the cm layer can pick the dedicated price-refusal template)
_PRICE = [
    re.compile(r"\bwen\b", _FLAGS),
    re.compile(r"\bmoon(ing|shot)?\b", _FLAGS),
    re.compile(r"\bto\s*\$?\d", _FLAGS),
    re.compile(r"\bprice\s+(prediction|target|forecast)\b", _FLAGS),
    re.compile(r"\b(eoy|eoq|end\s+of\s+year)\s+price\b", _FLAGS),
    re.compile(r"\bhow\s+high\b", _FLAGS),
    re.compile(r"\bwhere\s+(do|will)\s+(you\s+)?(see|this|it)\s+\w*\s*go", _FLAGS),
    re.compile(r"\$\s?\d[\d,.]*\s*(target|by\s+eoy|eoy)", _FLAGS),
]

# other charged hard-refusal / content-block shapes → reactive refusal
_REFUSAL = [
    re.compile(r"\bshould\s+i\s+(buy|sell|hold|ape|enter|exit|short|long)\b", _FLAGS),
    re.compile(r"\bgood\s+(buy|entry|investment|time\s+to\s+buy)\b", _FLAGS),
    re.compile(r"\blong\s+or\s+short\b", _FLAGS),
    re.compile(r"\bis\s+(this|it)\s+(over|under)valued\b", _FLAGS),
    re.compile(r"\bi(?:'m| am)\s+down\s+\d", _FLAGS),
    re.compile(r"\bi\s+(put|invested|aped)\s+(in\s+)?\$?\d", _FLAGS),
    re.compile(r"\bis\s+my\s+(position|bag|portfolio)\s+safe\b", _FLAGS),
    re.compile(r"\bis\s+(this|it)\s+(legal|a\s+security)\b", _FLAGS),
    re.compile(r"\bdo\s+i\s+owe\s+tax", _FLAGS),
    re.compile(r"\bany\s+alpha\b", _FLAGS),
    re.compile(r"\b(unannounced|non[-\s]?public|insider\s+info|inside\s+info)\b", _FLAGS),
    re.compile(r"\bignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?)\b", _FLAGS),
    re.compile(r"\byou\s+are\s+now\b", _FLAGS),
    re.compile(r"\b(system\s+prompt|your\s+config)\b", _FLAGS),
    re.compile(r"\bpretend\s+(you'?re|you\s+are|to\s+be)\b", _FLAGS),
    re.compile(r"\bdox+\b", _FLAGS),
    re.compile(r"\blaunder", _FLAGS),
]

# irreducible-fact lookups → calm slot-fill
_SLOTFILL = [
    re.compile(r"\bcontract\s+(address|addr|ca)\b", _FLAGS),
    re.compile(r"\b(what'?s|whats|wat)\s+the\s+(contract|ca)\b", _FLAGS),
    re.compile(r"\bvault\s+(address|addr|contract)\b", _FLAGS),
    re.compile(r"\baudit\s+(link|url|report|page)\b", _FLAGS),
    re.compile(r"\bofficial\s+(twitter|x|telegram|tg|discord|handle|account|link)\b", _FLAGS),
    re.compile(r"\bwhere('?s| is)\s+the\s+(contract|audit|vault)\b", _FLAGS),
    re.compile(r"\b(token|coin)\s+address\b", _FLAGS),
    re.compile(r"\b(drop|share|send|post|give|gimme)\s+(me\s+|the\s+|us\s+)?ca\b", _FLAGS),
    re.compile(r"\bca\s*\??\s*$", _FLAGS),
]

# "what's the current X" factual reads (vault state / buyback / tvl) → status
_STATUS = [
    re.compile(r"\b(tvl|nav)\b", _FLAGS),
    re.compile(r"\bvault\s+(balance|value|tvl|size|state|status)\b", _FLAGS),
    re.compile(r"\blast\s+buyback\b", _FLAGS),
    re.compile(r"\bbuyback\s+(log|status|amount|history)\b", _FLAGS),
    re.compile(r"\bhow\s+much\s+(is\s+)?(in\s+)?the\s+vault\b", _FLAGS),
    re.compile(r"\bcurrent\s+(vault|tvl|nav|allocation|holdings?)\b", _FLAGS),
]

# definitional asks → calm glossary
_GLOSSARY = [
    re.compile(r"\bwhat\s+(is|are|does)\s+\b", _FLAGS),
    re.compile(r"\bwhat'?s\s+(a|an|the\s+meaning|the\s+point)\b", _FLAGS),
    re.compile(r"\b(explain|define)\b", _FLAGS),
    re.compile(r"\bhow\s+does\s+\w+\s+work\b", _FLAGS),
    re.compile(r"\bwhat\s+do(es)?\s+\w+\s+mean\b", _FLAGS),
]

# greetings / social glue → calm welcome
_GREETING = [
    re.compile(r"^\s*(gm|gn|hi|hey|hello|yo|sup|hiya|hallo|greetings)\b", _FLAGS),
    re.compile(r"\b(just\s+)?(joined|here|arrived)\b", _FLAGS),
    re.compile(r"\b(new\s+here|first\s+time\s+here)\b", _FLAGS),
    re.compile(r"^\s*wassup\b", _FLAGS),
]


def _any(text: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(text) for p in patterns)


def classify(text: str) -> str:
    """Return one coarse routing category in CATEGORIES. Deterministic; offline.

    Precedence (highest-stakes first): price → refusal → slotfill → status →
    glossary → greeting → unknown. Charged categories win so a message that both
    greets and asks "wen moon" routes to the refusal path.
    """
    if not text or not text.strip():
        return "unknown"

    if _any(text, _PRICE):
        return "price"
    if _any(text, _REFUSAL):
        return "refusal"
    if _any(text, _SLOTFILL):
        return "slotfill"
    if _any(text, _STATUS):
        return "status"
    if _any(text, _GLOSSARY):
        return "glossary"
    if _any(text, _GREETING):
        return "greeting"
    return "unknown"
