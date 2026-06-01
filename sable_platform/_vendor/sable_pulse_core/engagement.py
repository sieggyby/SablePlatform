"""Stage-1 engagement heuristic — the cheap, deterministic engage/skip/ambiguous gate.

Before any routing or drafting, the CM pipeline asks: should the bot even look at
this message? `assess()` is the TEXT-ONLY half of that decision. It is a pure
function of the message text plus three cheap structural flags the relay already
knows (is it a reply to the bot, is the bot @-mentioned, the bot's own username).

  EngagementResult.decision ∈ {engage, skip, ambiguous}
    engage     — clearly worth a reply (direct address, a real question, charged
                 content the safety/refusal layer must see)
    skip       — clearly NOT worth a reply (low-content acks: "lol", "🔥", a bare
                 emoji, a single-word reaction) and not directed at the bot
    ambiguous  — could go either way; the cm layer escalates to HITL rather than
                 guess (matches the C0.1 exit probe: ambiguous → escalate)

IMPORTANT (MEGAPLAN C0.1 note): the stateful strong-skips — auto-silenced flagged
users, recent-reply throttling, founder-pre-emption — are **AutoCM-only**. They
depend on `autocm_flagged_users` + relay runtime state that the standalone
sable-pulse bot has no DB for, and are added AutoCM-side in C3.4a. This module is
deliberately the text-only heuristic and holds NO runtime state.

No network, no LLM, no telegram import. Deterministic: same inputs → same result.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_FLAGS = re.IGNORECASE

# Low-content acknowledgements / reactions that don't warrant a reply on their own
# (VOICE.md §3: "Reply to 'lol' / '🔥' / similar low-content unless directly addressed").
_LOW_CONTENT_WORDS = {
    "lol", "lmao", "lmfao", "rofl", "haha", "hahaha", "kek", "based", "fr",
    "gm", "gn", "ok", "okay", "k", "yes", "no", "nice", "cool", "wow", "wen",
    "true", "this", "same", "facts", "yep", "nope", "yup", "ty", "thanks",
    "thx", "ser", "wagmi", "ngmi", "fud", "bullish", "bearish", "🔥", "💯",
    "👍", "🚀", "😂", "🤣", "💀", "gg",
}

# Emoji-only / punctuation-only detector (covers a bare reaction message)
_EMOJI_OR_PUNCT_ONLY = re.compile(
    r"^[\s\W\d]*$", re.UNICODE
)

# A question shape (engage signal — questions are content the bot can act on)
_QUESTION = re.compile(r"\?", _FLAGS)

# Charged / chargeable lexicon — words that, even without a "?", mean the
# safety/refusal layer must see the message (so engagement must not skip it).
# This MUST cover SAFETY §1 (hard-refusal) AND §3 (universal content-block) vocab,
# so a charged message engages even when `hard_refusal_enabled` is off and the
# safety gate is bypassed — defense-in-depth behind cm.py's safety-first ordering.
_CHARGED = [
    re.compile(r"\b(buy|sell|hold|moon|wen|rug|scam|ponzi|dox|launder|alpha)\b", _FLAGS),
    re.compile(r"\bignore\s+(previous|prior|above)\b", _FLAGS),
    re.compile(r"\byou\s+are\s+now\b", _FLAGS),
    re.compile(r"\bsystem\s+prompt\b", _FLAGS),
    re.compile(r"\b(legal|security|tax(es)?)\b", _FLAGS),
    # §3 content-block vocab: adult content, allegation verbs, PII / doxxing.
    re.compile(r"\b(nsfw|explicit|erotic|nude(s)?|porn(ography)?|sext(ing)?)\b", _FLAGS),
    re.compile(r"\b(fraud|embezzled|stole|scammer|scammed|rugged|laundered)\b", _FLAGS),
    re.compile(r"\b(real\s+name|home\s+address|personal\s+(info|details)|phone\s+number|email\s+address)\b", _FLAGS),
    re.compile(r"\bunmask\b", _FLAGS),
]

# Question/answerable lead words — even without a "?", these read as a real ask.
_INTERROGATIVE_LEAD = re.compile(
    r"^\s*(what|where|when|why|how|who|which|is|are|can|could|should|do|does|did|will|whats|what's|hows)\b",
    _FLAGS,
)


@dataclass
class EngagementResult:
    decision: str  # "engage" | "skip" | "ambiguous"
    reason: str
    is_directed: bool  # reply-to-bot OR @-mention


def _strip_mention(text: str, bot_username: str | None) -> str:
    if bot_username:
        handle = bot_username.lstrip("@")
        text = re.sub(rf"@{re.escape(handle)}\b", " ", text, flags=_FLAGS)
    return text


def _is_low_content(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if _EMOJI_OR_PUNCT_ONLY.match(stripped):
        return True
    # single short token that is a known low-content reaction
    tokens = stripped.split()
    if len(tokens) == 1:
        return tokens[0].strip(".!?,").lower() in _LOW_CONTENT_WORDS
    # two-token reactions like "lol nice" / "based fr"
    if len(tokens) <= 2 and all(t.strip(".!?,").lower() in _LOW_CONTENT_WORDS for t in tokens):
        return True
    return False


def _looks_charged(text: str) -> bool:
    return any(p.search(text) for p in _CHARGED)


def _looks_like_question(text: str) -> bool:
    return bool(_QUESTION.search(text)) or bool(_INTERROGATIVE_LEAD.match(text))


def assess(
    text: str,
    *,
    is_reply_to_bot: bool,
    is_mention: bool,
    bot_username: str | None,
) -> EngagementResult:
    """Decide engage / skip / ambiguous from text + structural flags only.

    Rules (deterministic, evaluated in order):
      1. Empty / whitespace → skip.
      2. Charged content (buy/sell/rug/injection/legal + the §3 content-block vocab:
         nsfw/explicit, fraud/stole/scammer, real-name/PII, unmask…) → engage, even a
         low-content shape, because the safety layer must see it.
      3. Directly addressed (reply-to-bot OR @mention): a question or substantive
         body → engage; a bare low-content reaction → ambiguous (someone pinged the
         bot with "lol" — let a human decide).
      4. Not directed: low-content reaction → skip; a clear question → engage;
         otherwise → ambiguous.
    """
    raw = text or ""
    directed = bool(is_reply_to_bot or is_mention)
    body = _strip_mention(raw, bot_username).strip()

    if not raw.strip():
        return EngagementResult("skip", "empty", directed)

    # 2. Charged content is always worth seeing (safety gate downstream).
    if _looks_charged(body or raw):
        return EngagementResult("engage", "charged", directed)

    low = _is_low_content(body if directed else raw)

    if directed:
        if low and not _looks_like_question(body):
            # pinged the bot with a bare reaction — let a human decide
            return EngagementResult("ambiguous", "directed_low_content", directed)
        return EngagementResult("engage", "directed", directed)

    # 4. Undirected.
    if low:
        return EngagementResult("skip", "low_content_undirected", directed)
    if _looks_like_question(raw):
        return EngagementResult("engage", "question", directed)
    return EngagementResult("ambiguous", "undirected_statement", directed)
