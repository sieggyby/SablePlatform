"""SlotFillKB — the deterministic fact registry + glossary lookup.

Two stores, both irreducible and offline:

  constants  — the highest-stakes facts (SAFETY §2.5 "exact-match or slot-fill"):
               contract address, vault address, audit URL, official handles.
               These are NEVER LLM-generated; the template engine substitutes the
               literal string. `constant(key)` is a flat dict lookup; `match_slotfill`
               maps a free-text question to the right key.
  glossary   — definitional terms (SAFETY §2.5 "loose" stakes): "what is ERC-4626",
               "what's a vault," etc. `match_glossary` maps free text → (term, def).

This mirrors the AutoCM `autocm_kb_constants` registry shape but is file/dict-backed
for the standalone sable-pulse MVP. No network, no LLM, no telegram. Deterministic:
same text → same lookup, every time.

Usage:

    kb = SlotFillKB(constants={...}, glossary={...})
    kb.constant("contract_address")              # -> "0x6502…" or None
    kb.match_slotfill("what's the contract address")  # -> ("contract_address", "0x65…")
    kb.match_glossary("what is erc-4626")              # -> ("erc-4626", "a token vault standard…")
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_FLAGS = re.IGNORECASE

# Maps a slot-fill constant key → the patterns whose match routes a question to it.
# Keys correspond to entries callers put in `constants`. A question matches a key
# only if that key is present in the constants dict (so a tenant without a vault
# never resolves vault questions).
_SLOTFILL_PATTERNS: dict[str, list[re.Pattern]] = {
    "contract_address": [
        re.compile(r"\bcontract\s+(address|addr|ca)\b", _FLAGS),
        re.compile(r"\b(what'?s|whats|wat)\s+the\s+(contract|ca)\b", _FLAGS),
        re.compile(r"\b(token|coin)\s+address\b", _FLAGS),
        re.compile(r"\bwhere('?s| is)\s+the\s+contract\b", _FLAGS),
        # "CA" crypto-TG shorthand for contract address — bare token, drop/share verbs,
        # or trailing "ca?" — kept specific so it won't match "California".
        re.compile(r"\b(drop|share|send|post|give|gimme|wat|wats|what'?s|whats)\s+(me\s+|the\s+|us\s+)?ca\b", _FLAGS),
        re.compile(r"\bca\s*\??\s*$", _FLAGS),
    ],
    "vault_address": [
        re.compile(r"\bvault\s+(address|addr|contract)\b", _FLAGS),
        re.compile(r"\bwhere('?s| is)\s+the\s+vault\s+(address|contract)\b", _FLAGS),
    ],
    "audit_url": [
        re.compile(r"\baudit\s+(link|url|report|page|doc)\b", _FLAGS),
        re.compile(r"\b(is\s+there\s+an?|where('?s| is)\s+the)\s+audit\b", _FLAGS),
        re.compile(r"\bwho\s+audited\b", _FLAGS),
    ],
    "official_twitter": [
        re.compile(r"\bofficial\s+(twitter|x|account)\b", _FLAGS),
        re.compile(r"\b(twitter|x)\s+(handle|account|link)\b", _FLAGS),
    ],
    "official_telegram": [
        re.compile(r"\bofficial\s+(telegram|tg)\b", _FLAGS),
        re.compile(r"\b(telegram|tg)\s+(group|link|channel)\b", _FLAGS),
    ],
    "official_discord": [
        re.compile(r"\bofficial\s+discord\b", _FLAGS),
        re.compile(r"\bdiscord\s+(link|invite|server)\b", _FLAGS),
    ],
    "website": [
        re.compile(r"\bofficial\s+(website|site|link)\b", _FLAGS),
        re.compile(r"\b(website|homepage)\b", _FLAGS),
    ],
}

# Deterministic precedence when several slot-fill keys match (most-specific first).
_SLOTFILL_PRECEDENCE = (
    "contract_address",
    "vault_address",
    "audit_url",
    "official_twitter",
    "official_telegram",
    "official_discord",
    "website",
)


def _normalize(term: str) -> str:
    """Lowercase, collapse separators — so 'ERC-4626' / 'erc 4626' / 'erc4626' align."""
    return re.sub(r"[\s\-_]+", "", term.strip().lower())


@dataclass
class SlotFillKB:
    constants: dict[str, str] = field(default_factory=dict)
    glossary: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Precompute a normalized glossary index for whole-token matching.
        self._glossary_index: dict[str, tuple[str, str]] = {}
        for term, definition in self.glossary.items():
            self._glossary_index[_normalize(term)] = (term, definition)

    # ---- constants ----------------------------------------------------------

    def constant(self, key: str) -> str | None:
        """Literal lookup of an irreducible fact. None if absent."""
        return self.constants.get(key)

    def match_slotfill(self, text: str) -> tuple[str, str] | None:
        """Map free-text to (key, value) for a slot-fill constant, or None.

        Only resolves keys that are actually present in `constants` — a tenant with
        no vault address will never answer a vault question (it escalates instead).
        """
        if not text:
            return None
        fired: set[str] = set()
        for key, patterns in _SLOTFILL_PATTERNS.items():
            if key not in self.constants:
                continue
            if any(p.search(text) for p in patterns):
                fired.add(key)
        if not fired:
            return None
        for key in _SLOTFILL_PRECEDENCE:
            if key in fired:
                return key, self.constants[key]
        # Any non-precedence key (custom) — deterministic by sorted order.
        key = sorted(fired)[0]
        return key, self.constants[key]

    # ---- glossary -----------------------------------------------------------

    def match_glossary(self, text: str) -> tuple[str, str] | None:
        """Map free-text to (term, definition) for the longest matching glossary
        term contained in the text, or None.

        Longest-term-wins so 'erc-4626 vault' prefers 'erc-4626' over 'vault'.
        """
        if not text or not self._glossary_index:
            return None
        norm_text = _normalize(text)
        hits: list[tuple[str, str]] = []
        for norm_term, (term, definition) in self._glossary_index.items():
            if norm_term and norm_term in norm_text:
                hits.append((term, definition))
        if not hits:
            return None
        # Longest term wins; ties broken by alphabetical term for determinism.
        hits.sort(key=lambda td: (-len(_normalize(td[0])), td[0]))
        return hits[0]
