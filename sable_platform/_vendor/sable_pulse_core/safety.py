"""Hard-refusal + universal content-block detector — the deterministic safety bank.

This is the single donor source for the downstream AutoCM `gate/safety` layer
(per MEGAPLAN C0.1 + SableAutoCM `docs/SAFETY.md`). It is a *pure*, offline,
regex-driven scanner: given a piece of user text, it reports whether the text
trips a hard-refusal category (SAFETY §1) or a universal content block (SAFETY §3).

It carries no LLM, no network, no Telegram. It only *detects* — the calibrated
refusal *wording* lives in the reactive NULO template bank (`core/nulo.py` +
`personas/<tenant>/nulo/reactive.yaml`); all hard refusals trigger the reactive
register by design (SAFETY §0).

Coverage (every category here gets a firing test fixture — under-coverage here
under-protects every downstream bot):

  §1 hard-refusal categories
    - price_prediction        wen / moon / $X target / EOY price / where's this going
    - financial_advice        should I buy/sell/hold / good entry / long or short / TP
    - personal_portfolio      I put $X in / I'm down Y% / is my position safe
    - legal_regulatory        is this legal / is this a security / do I owe taxes
    - insider_information      any alpha / unannounced / not-public roadmap
    - prompt_injection        ignore previous instructions / system prompt / you are now X

  §3 universal content blocks
    - pii_request             personal info / real name / address / phone of a user
    - doxxing                 dox / expose / find out who someone really is
    - allegations             accusation / "X is a scammer/criminal" against a named person
    - adult_content           explicit sexual content
    - illegal_ofac            money laundering / OFAC / sanctioned-entity dealings
    - competitor_disparage    speculation that a competitor "is a rug / is rugging"

API:

    m = check_refusal(text)
    if m is not None:
        m.category   # canonical category key (one of CATEGORIES)
        m.kind       # "hard_refusal" | "content_block"
        m.trigger    # the literal substring that fired (for audit/logging)
        m.register   # always "reactive" — refusals are charged content by definition
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---- category taxonomy ------------------------------------------------------

HARD_REFUSAL_CATEGORIES = (
    "price_prediction",
    "financial_advice",
    "personal_portfolio",
    "legal_regulatory",
    "insider_information",
    "prompt_injection",
)

CONTENT_BLOCK_CATEGORIES = (
    "pii_request",
    "doxxing",
    "allegations",
    "adult_content",
    "illegal_ofac",
    "competitor_disparage",
)

CATEGORIES = HARD_REFUSAL_CATEGORIES + CONTENT_BLOCK_CATEGORIES


@dataclass
class RefusalMatch:
    """A single fired safety detection. `register` is always reactive (SAFETY §0)."""
    category: str
    kind: str  # "hard_refusal" | "content_block"
    trigger: str
    register: str = "reactive"


# ---- pattern banks ----------------------------------------------------------
# Each entry: (category, compiled_pattern). Order matters only for which trigger
# string is reported when several within a category match; the first category in
# CATEGORIES order wins across categories (deterministic). Patterns are authored
# to be specific enough to avoid the most obvious false-fires while staying
# resilient to phrasing — precision vs recall is tuned via the SAFETY §6 loop.

_FLAGS = re.IGNORECASE

# §1 — hard refusals
_HARD_PATTERNS: list[tuple[str, re.Pattern]] = [
    # price prediction
    ("price_prediction", re.compile(r"\bwen\b", _FLAGS)),
    ("price_prediction", re.compile(r"\bmoon(ing|shot)?\b", _FLAGS)),
    ("price_prediction", re.compile(r"\bto\s*\$?\d", _FLAGS)),               # "to $1", "to 10x"
    ("price_prediction", re.compile(r"\$\s?\d[\d,.]*\s*(target|by|eoy|eoq)", _FLAGS)),
    ("price_prediction", re.compile(r"\bprice\s+(prediction|target|forecast)\b", _FLAGS)),
    ("price_prediction", re.compile(r"\b(eoy|eoq|end\s+of\s+year)\s+price\b", _FLAGS)),
    ("price_prediction", re.compile(r"\bwhere\s+(do|will|are)\s+(you\s+)?(see|this|we|it)\s+\w*\s*go", _FLAGS)),
    ("price_prediction", re.compile(r"\bhow\s+high\s+(can|will|could)\b", _FLAGS)),
    # financial / trading advice
    ("financial_advice", re.compile(r"\bshould\s+i\s+(buy|sell|hold|ape|enter|exit|short|long)\b", _FLAGS)),
    ("financial_advice", re.compile(r"\bis\s+(now|this|it)\s+a?\s*good\s+(entry|time\s+to\s+buy|buy)\b", _FLAGS)),
    ("financial_advice", re.compile(r"\bis\s+(this|it)\s+(over|under)valued\b", _FLAGS)),
    ("financial_advice", re.compile(r"\blong\s+or\s+short\b", _FLAGS)),
    ("financial_advice", re.compile(r"\b(tp|take[-\s]?profit|stop[-\s]?loss)\s+(target|level|at)\b", _FLAGS)),
    ("financial_advice", re.compile(r"\bgood\s+(buy|entry|investment)\b", _FLAGS)),
    # personal portfolio
    ("personal_portfolio", re.compile(r"\bi\s+(put|invested|aped|threw)\s+(in\s+)?\$?\d", _FLAGS)),
    ("personal_portfolio", re.compile(r"\bi(?:'m| am)\s+down\s+\d", _FLAGS)),
    ("personal_portfolio", re.compile(r"\bi(?:'m| am)\s+down\s+\w+\s*%", _FLAGS)),
    ("personal_portfolio", re.compile(r"\bis\s+my\s+(position|bag|portfolio|investment)\s+safe\b", _FLAGS)),
    ("personal_portfolio", re.compile(r"\bwhat\s+(should|do)\s+i\s+do\s+with\s+my\b", _FLAGS)),
    ("personal_portfolio", re.compile(r"\bmy\s+(position|bag|portfolio)\b.*\?", _FLAGS)),
    # legal / regulatory opinion
    ("legal_regulatory", re.compile(r"\bis\s+(this|it)\s+legal\b", _FLAGS)),
    ("legal_regulatory", re.compile(r"\bis\s+(this|it)\s+a\s+security\b", _FLAGS)),
    ("legal_regulatory", re.compile(r"\bdo\s+i\s+owe\s+(taxes?|tax)\b", _FLAGS)),
    ("legal_regulatory", re.compile(r"\bis\s+(this|it)\s+(legal|allowed|permitted)\s+in\b", _FLAGS)),
    ("legal_regulatory", re.compile(r"\b(tax|legal|regulatory)\s+(implications?|advice|opinion)\b", _FLAGS)),
    ("legal_regulatory", re.compile(r"\bhow\s+(do|should)\s+i\s+(report|file)\s+.*\btax", _FLAGS)),
    # insider information requests
    ("insider_information", re.compile(r"\bany\s+alpha\b", _FLAGS)),
    ("insider_information", re.compile(r"\b(what'?s|whats)\s+(the\s+)?team\s+working\s+on(\s+next)?\b", _FLAGS)),
    ("insider_information", re.compile(r"\b(anything|something)\s+(coming\s+up|planned|in\s+the\s+works)\b.*\b(not\s+announced|unannounced|secret|public)\b", _FLAGS)),
    ("insider_information", re.compile(r"\b(not\s+(yet\s+)?announced|unannounced|non[-\s]?public|inside\s+info|insider\s+info)\b", _FLAGS)),
    ("insider_information", re.compile(r"\bany\s+(secret|hidden|unreleased)\s+\w+\b", _FLAGS)),
    ("insider_information", re.compile(r"\bgive\s+me\s+(the\s+)?alpha\b", _FLAGS)),
    # prompt injection attempts
    ("prompt_injection", re.compile(r"\bignore\s+(all\s+)?(previous|prior|above|the)\s+(instructions?|prompts?|rules?)\b", _FLAGS)),
    ("prompt_injection", re.compile(r"\bdisregard\s+(all\s+)?(previous|prior|your)\s+\w+", _FLAGS)),
    ("prompt_injection", re.compile(r"\byou\s+are\s+now\b", _FLAGS)),
    ("prompt_injection", re.compile(r"\b(what(?:'s| is)|show\s+me)\s+(your\s+)?(system\s+prompt|config(uration)?|instructions?)\b", _FLAGS)),
    ("prompt_injection", re.compile(r"\bpretend\s+(you'?re|you\s+are|to\s+be)\b", _FLAGS)),
    ("prompt_injection", re.compile(r"\bact\s+as\s+(if\s+)?(you|a)\b", _FLAGS)),
    ("prompt_injection", re.compile(r"\b(reveal|print|repeat|output)\s+(your\s+)?(system\s+prompt|instructions?|prompt)\b", _FLAGS)),
    ("prompt_injection", re.compile(r"\bdeveloper\s+mode\b", _FLAGS)),
]

# §3 — universal content blocks
_BLOCK_PATTERNS: list[tuple[str, re.Pattern]] = [
    # PII requests about a user
    ("pii_request", re.compile(r"\b(real\s+name|home\s+address|phone\s+number|email\s+address|wallet\s+address)\s+of\b", _FLAGS)),
    ("pii_request", re.compile(r"\bwhat'?s?\s+(his|her|their|that\s+(guy|user|person)'?s?)\s+(real\s+name|address|phone|email|number)\b", _FLAGS)),
    ("pii_request", re.compile(r"\bgive\s+me\s+(his|her|their|the\s+user'?s?)\s+(personal\s+)?(info|details|address|phone|email)\b", _FLAGS)),
    ("pii_request", re.compile(r"\bwhere\s+does\s+\w+\s+live\b", _FLAGS)),
    # doxxing
    ("doxxing", re.compile(r"\bdox+(x?ing|x?ed)?\b", _FLAGS)),
    ("doxxing", re.compile(r"\bexpose\s+(who\s+)?(he|she|they|this\s+(guy|person|user))\s+(really\s+)?(is|are)\b", _FLAGS)),
    ("doxxing", re.compile(r"\b(find|figure)\s+out\s+who\s+\w+\s+(really\s+)?is\b", _FLAGS)),
    ("doxxing", re.compile(r"\bunmask\b", _FLAGS)),
    ("doxxing", re.compile(r"\bleak\s+(his|her|their)\s+(identity|info|details)\b", _FLAGS)),
    # allegations against named individuals
    ("allegations", re.compile(r"\b(is|are)\s+\w+\s+(a\s+)?(scammer|fraud|criminal|crook|thief|liar|pedo|pedophile)\b", _FLAGS)),
    ("allegations", re.compile(r"\b\w+\s+(is|are)\s+(a\s+)?(scammer|fraudster|criminal|crook|pedophile)\b", _FLAGS)),
    ("allegations", re.compile(r"\b\w+\s+(stole|scammed|rugged|embezzled|laundered)\b", _FLAGS)),
    ("allegations", re.compile(r"\b(accuse|allegation|allege)\w*\b.*\b(stole|fraud|scam|crime)\b", _FLAGS)),
    # adult content
    ("adult_content", re.compile(r"\b(nsfw|explicit\s+(sex|content)|porn(ography)?|nude(s)?|sexual(ly)?)\b", _FLAGS)),
    ("adult_content", re.compile(r"\bwrite\s+(me\s+)?(an?\s+)?(erotic|sexual)\b", _FLAGS)),
    ("adult_content", re.compile(r"\bsext(ing)?\b", _FLAGS)),
    # illegal / OFAC / sanctions
    ("illegal_ofac", re.compile(r"\b(money\s+)?launder(ing|ed)?\b", _FLAGS)),
    ("illegal_ofac", re.compile(r"\bofac\b", _FLAGS)),
    ("illegal_ofac", re.compile(r"\bsanction(ed|s)\b", _FLAGS)),
    ("illegal_ofac", re.compile(r"\b(evade|avoid|dodge)\s+(taxes?|sanctions?|kyc|aml)\b", _FLAGS)),
    ("illegal_ofac", re.compile(r"\bhow\s+(do|to|can)\s+i?\s*\w*\s*(mix|tumble|obfuscate)\s+(coins?|funds?|crypto)\b", _FLAGS)),
    ("illegal_ofac", re.compile(r"\bhide\s+(funds?|money|crypto)\s+from\b", _FLAGS)),
    # disparaging-competitor speculation (factual comparison OK; "X is rugged" speculation not)
    ("competitor_disparage", re.compile(r"\b\w+\s+is\s+(going\s+to\s+|gonna\s+|about\s+to\s+)?(rug|rugging|getting\s+rugged|a\s+rug)\b", _FLAGS)),
    ("competitor_disparage", re.compile(r"\bis\s+\w+\s+(going\s+to\s+|gonna\s+)?rug\b", _FLAGS)),
    ("competitor_disparage", re.compile(r"\b\w+\s+(is|are)\s+(a\s+)?(ponzi|scam\s+coin|exit\s+scam)\b", _FLAGS)),
    ("competitor_disparage", re.compile(r"\bwill\s+\w+\s+(rug|exit\s+scam|collapse)\b", _FLAGS)),
]


def _first_match(text: str, patterns: list[tuple[str, re.Pattern]]) -> tuple[str, str] | None:
    """Return (category, trigger) for the first category (in CATEGORIES order) that
    fires, scanning that category's patterns for the literal matched substring."""
    # Resolve, per category, the *first* fired pattern's match string.
    fired: dict[str, str] = {}
    for cat, pat in patterns:
        if cat in fired:
            continue
        mo = pat.search(text)
        if mo:
            fired[cat] = mo.group(0).strip()
    if not fired:
        return None
    # Deterministic precedence: CATEGORIES declaration order.
    for cat in CATEGORIES:
        if cat in fired:
            return cat, fired[cat]
    return None


def check_refusal(text: str) -> RefusalMatch | None:
    """Scan `text` for any §1 hard-refusal or §3 content-block trigger.

    Returns the highest-precedence `RefusalMatch` (precedence = CATEGORIES order:
    hard refusals before content blocks, then declaration order within), or None
    if the text is clean. Always offline and deterministic.
    """
    if not text:
        return None

    hard = _first_match(text, _HARD_PATTERNS)
    block = _first_match(text, _BLOCK_PATTERNS)

    if hard is None and block is None:
        return None

    # Hard refusals (§1) take precedence over content blocks (§3) when both fire,
    # mirroring CATEGORIES order (hard categories declared first).
    if hard is not None:
        return RefusalMatch(category=hard[0], kind="hard_refusal", trigger=hard[1])
    # block is not None here
    return RefusalMatch(category=block[0], kind="content_block", trigger=block[1])
