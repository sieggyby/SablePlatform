"""Citation / hallucination gate (DESIGN §4 ``gate/citation_check``) — C3.5a.

The tiered hallucination-prevention gate (SAFETY.md §2.5). A draft is checked at
ONE of three stakes tiers, chosen per category; the higher the stakes, the
stricter the grounding requirement:

  * **loose** (:data:`TIER_LOOSE`) — low-stakes (greeting / glossary / banter).
    Soft: NO programmatic citation check. The draft always passes
    (``passed=True``); a missing citation is not a rejection at this tier. (The
    LLM is still steered to ground its answer, but the gate does not auto-reject.)

  * **citation-required** (:data:`TIER_CITATION_REQUIRED`) — medium-stakes
    (mechanics / status / sentiment). The draft MUST carry inline ``[chunk_id]``
    references, and EVERY referenced id must be a real, retrieval-surfaced chunk
    for THIS client (validated against ``autocm_kb_chunks.id`` ∩ the
    ``available_chunk_ids`` the retriever surfaced). A draft with zero citations,
    or any citation that is not a valid surfaced chunk, is AUTO-REJECTED
    (``passed=False`` → forced HITL / re-draft).

  * **exact-match-or-slot-fill** (:data:`TIER_EXACT_MATCH`) — highest-stakes
    irreducibles (contract address / vault address / audit URL / official
    handles — SAFETY §2.5 / trust category). The answer must be EITHER a literal
    slot-fill value from ``autocm_kb_constants`` (the operator-seeded irreducible,
    NEVER LLM-generated — SAFETY §2.5 models this as template substitution,
    ``Contract: {{contract_address}}``, so the normalized answer must EQUAL the
    constant value, not merely contain it) OR an exact substring match of a
    surfaced KB chunk's text (an exact quote of grounded content). ANY deviation
    (a paraphrase, a single transposed character in a contract address, or a
    fabricated claim riding alongside one correct irreducible) AUTO-REJECTS —
    these facts are never approximated.

Category → tier mapping (:func:`tier_for_category`) is code-owned and mirrors the
SAFETY.md §2.5 enforcement table ROW-FOR-ROW: the trust category (exact-match
enforcement, floor 0.92) is exact-match; the §2.5 citation-required row (mechanics,
status, FUD_borderline, sentiment_negative, partnership_unannounced) is
citation-required; only the §2.5 loose row (greeting / glossary / off-topic /
meta_about_bot / catchphrase) is loose. FUD_borderline and sentiment_negative are
CITATION-REQUIRED, not loose — a fabricated fact in an auto-sent FUD rebuttal or
angry-holder reassurance is the §2.5 "single worst failure mode" and must hit the
gate.

Pure-ish: the loose + citation-required legs are pure over their inputs; the
exact-match leg reads ``autocm_kb_constants`` + the surfaced chunk texts via a
``Connection`` (no LLM / network). Inline-ref parsing is a deterministic regex.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

# ---------------------------------------------------------------------------
# The three SAFETY §2.5 stakes tiers
# ---------------------------------------------------------------------------
TIER_LOOSE = "loose"
TIER_CITATION_REQUIRED = "citation_required"
TIER_EXACT_MATCH = "exact_match_or_slot_fill"

CITATION_TIERS = (TIER_LOOSE, TIER_CITATION_REQUIRED, TIER_EXACT_MATCH)

# Category → citation tier. This map MIRRORS THE SAFETY.md §2.5 enforcement table
# ROW-FOR-ROW (docs/SAFETY.md:71-73) — it is the §2.5 table expressed as code, and
# the placement of every category here is sourced from that one table so the two
# cannot drift (the tier-mapping test pins each row). Categories absent from this
# map default to citation-required (the safe middle — a draft for an unmapped
# factual category must still ground its claims).
_CATEGORY_TIER: dict[str, str] = {
    # --- §2.5 "Loose" row (no programmatic check). The §2.5 Loose row is EXACTLY:
    #     greeting, glossary, off-topic, meta_about_bot, catchphrase — and NOTHING
    #     else. sentiment_negative / FUD_borderline are NOT here (see below).
    "greeting": TIER_LOOSE,
    "glossary": TIER_LOOSE,
    "catchphrase_repetition": TIER_LOOSE,  # §2.5 "catchphrase"
    "meta_about_bot": TIER_LOOSE,
    # --- §2.5 "Citation-required" row — KB-grounded factual answers that MUST carry
    #     validated inline [chunk_id] refs. The §2.5 row is EXACTLY: mechanics,
    #     status, FUD_borderline, sentiment_negative, partnership_unannounced.
    #     FUD_borderline + sentiment_negative compose substantive public drafts and
    #     ARE promotable to auto, so a fabricated TVL/buyback/audit claim in an
    #     auto-sent FUD rebuttal or angry-holder reassurance MUST hit the citation
    #     gate (the §2.5 "single worst failure mode") — they belong here, not Loose.
    "mechanics": TIER_CITATION_REQUIRED,
    "status": TIER_CITATION_REQUIRED,
    "FUD_borderline": TIER_CITATION_REQUIRED,
    "sentiment_negative": TIER_CITATION_REQUIRED,
    "partnership_unannounced": TIER_CITATION_REQUIRED,
    # price is a live on-chain read (CLASSIFIER §2); a factual numeric answer must
    # still be grounded, so it carries citation-required (not in the §2.5 Loose row).
    "price": TIER_CITATION_REQUIRED,
    "operational_complaint": TIER_CITATION_REQUIRED,
    # --- §2.5 "Exact-match or slot-fill" row — irreducibles. The §2.5 row reads
    #     "trust, regulatory, anything touching contract addresses / audit URLs /
    #     legal status". Only `trust` is mapped here because there is no `regulatory`
    #     classifier category, and the legal/regulatory factual answer is covered
    #     UPSTREAM by the safety gate's tier-1 hard refusal (`legal`,
    #     classifier/categories.py:187 — it REFUSES rather than emitting a
    #     citation-checkable factual draft), not by a citation tier. A FUTURE
    #     non-refusal legal/regulatory-status category MUST be added here (→
    #     TIER_EXACT_MATCH) rather than left to DEFAULT_TIER, which is the strictly
    #     weaker citation-required tier.
    "trust": TIER_EXACT_MATCH,
}

#: the safe default tier for a category not explicitly mapped above.
DEFAULT_TIER = TIER_CITATION_REQUIRED

# Inline citation ref: a bracketed integer chunk id, e.g. "[42]". Allows optional
# surrounding whitespace inside the brackets ("[ 42 ]"). Multiple refs per draft.
_INLINE_REF = re.compile(r"\[\s*(\d+)\s*\]")


def tier_for_category(category: Optional[str]) -> str:
    """Resolve the SAFETY §2.5 stakes tier for a category (safe default).

    A known category maps to its declared tier; an unknown / None category falls
    back to :data:`DEFAULT_TIER` (citation-required) — an unmapped factual answer
    must still be grounded, never silently treated as loose.
    """
    if not category:
        return DEFAULT_TIER
    return _CATEGORY_TIER.get(category, DEFAULT_TIER)


def parse_inline_citations(draft_text: str) -> List[int]:
    """Extract the inline ``[chunk_id]`` references from a draft, in order.

    Returns the integer chunk ids (deduplicated, first-seen order). A draft with
    no bracketed-integer refs yields an empty list — which the citation-required
    tier treats as "no citation" (auto-reject).
    """
    seen: set[int] = set()
    out: List[int] = []
    for m in _INLINE_REF.finditer(draft_text or ""):
        cid = int(m.group(1))
        if cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


@dataclass(frozen=True)
class CitationVerdict:
    """The citation gate's decision.

    ``passed`` False ⇒ AUTO-REJECT (the draft must NOT be published; force HITL /
    re-draft). ``tier`` records which stakes tier was applied; ``reason`` is a
    stable code; ``cited_chunk_ids`` is what the draft referenced; ``invalid_ids``
    are the cited ids that did NOT resolve to a surfaced chunk (citation-required
    tier). The fields are carried so the rejection is auditable / explainable.
    """

    passed: bool
    tier: str
    reason: str
    cited_chunk_ids: List[int] = field(default_factory=list)
    invalid_ids: List[int] = field(default_factory=list)


# Reason codes (stable).
REASON_LOOSE_OK = "loose_no_check"
REASON_CITED_OK = "citations_valid"
REASON_NO_CITATIONS = "no_citations"
REASON_INVALID_CITATIONS = "invalid_citations"
REASON_EXACT_OK = "exact_match_or_slotfill"
REASON_EXACT_DEVIATION = "exact_match_deviation"


def check_citations(
    draft_text: str,
    cited_chunk_ids: Sequence[int],
    available_chunk_ids: Sequence[int],
    *,
    tier: str = TIER_CITATION_REQUIRED,
) -> CitationVerdict:
    """Tiered citation check (loose / citation-required) over already-known ids.

    For the **loose** tier the draft always passes (no programmatic check). For
    **citation-required** the draft must carry at least one citation AND every
    cited id must be in ``available_chunk_ids`` (the retrieval-surfaced set for
    this client) — any missing-citation or invalid-id draft AUTO-REJECTS.

    Citations are taken from ``cited_chunk_ids`` when supplied (the structured
    citation list the drafter recorded), else parsed from the inline ``[id]`` refs
    in ``draft_text``. The **exact-match** tier needs DB access and is REFUSED here
    (``ValueError``) — it must be routed through :func:`check_citations_db`. This is
    enforced in code, not merely by docstring convention: the SAFETY §2.5
    highest-stakes tier (contract addresses / audit URLs / official handles) must
    NEVER fall through to the strictly weaker citation-required rule while the
    verdict mislabels itself as having passed the exact-match check — a fabricated
    irreducible carrying one valid ``[chunk_id]`` would otherwise be auto-APPROVED
    and reported as ``tier='exact_match_or_slot_fill'`` (fail-open). §2.5 requires
    ANY deviation at this tier to AUTO-REJECT, which only the DB path can decide.
    """
    if tier == TIER_EXACT_MATCH:
        raise ValueError(
            "exact-match tier requires a Connection; use check_citations_db"
        )

    cited = list(cited_chunk_ids) if cited_chunk_ids else parse_inline_citations(draft_text)

    if tier == TIER_LOOSE:
        return CitationVerdict(
            passed=True, tier=TIER_LOOSE, reason=REASON_LOOSE_OK, cited_chunk_ids=cited
        )

    # citation-required (the only non-loose tier reachable here; exact-match is
    # refused above so it can never silently downgrade to this weaker rule).
    if not cited:
        return CitationVerdict(
            passed=False,
            tier=tier,
            reason=REASON_NO_CITATIONS,
            cited_chunk_ids=[],
        )
    available = set(int(c) for c in available_chunk_ids)
    invalid = [c for c in cited if int(c) not in available]
    if invalid:
        return CitationVerdict(
            passed=False,
            tier=tier,
            reason=REASON_INVALID_CITATIONS,
            cited_chunk_ids=cited,
            invalid_ids=invalid,
        )
    return CitationVerdict(
        passed=True,
        tier=tier,
        reason=REASON_CITED_OK,
        cited_chunk_ids=cited,
    )


def _valid_chunk_ids_for_client(
    conn: Connection, client_id: int, candidate_ids: Sequence[int]
) -> set[int]:
    """Subset of ``candidate_ids`` that are real ACTIVE chunks for this client.

    Validates against ``autocm_kb_chunks.id`` scoped to ``client_id`` and
    ``status='active'`` — a stale/wrong chunk is NOT a valid citation target, and a
    chunk belonging to another client is silently excluded (KB_DESIGN §6 per-client
    isolation).
    """
    ids = [int(c) for c in candidate_ids]
    if not ids:
        return set()
    rows = conn.execute(
        text(
            "SELECT id FROM autocm_kb_chunks "
            "WHERE client_id = :c AND status = 'active' AND id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"c": client_id, "ids": ids},
    ).fetchall()
    return {int(r[0]) for r in rows}


def _exact_match_in_corpus(
    conn: Connection, client_id: int, answer: str, available_chunk_ids: Sequence[int]
) -> bool:
    """True iff ``answer`` slot-fills a constant OR exactly quotes a surfaced chunk.

    The exact-match leg of the SAFETY §2.5 highest-stakes tier:

      * **slot-fill** — SAFETY §2.5 models this as literal template substitution
        (``Contract: {{contract_address}}`` → the registry value IS the answer; the
        LLM never generates the fact). So the irreducible must BE the answer, not
        merely co-occur in it: the normalized answer must EQUAL a constant value
        (whole-answer match), NOT contain it as a substring. This refuses the two
        failure modes the loose ``cval in needle`` substring direction allowed:
        (a) a fabricated high-stakes claim riding ALONGSIDE one correct irreducible
        (``"the contract is 0x… and the audit was by FakeAuditor with 9000% APY
        guaranteed"`` — the contract value is a substring, so the loose leg cleared
        the whole fabricated draft), and (b) a short constant colliding as an
        incidental substring (ticker ``RM`` inside ``rmbling``). Under whole-answer
        EQUAL neither clears the tier. — OR
      * **exact KB match** — ``answer`` (normalized) is a substring of a surfaced
        ACTIVE chunk's text (an exact quote of grounded content; the LLM is quoting,
        not inventing, so co-occurrence with the chunk is the intended semantics
        here, unlike the operator-seeded slot-fill leg).

    Normalization is whitespace-collapse + case-fold (so trailing whitespace /
    casing of the surrounding prose is not a "deviation"); the irreducible value
    (a contract address) is matched literally — a transposed character changes the
    value and fails the equality, which is the intent.
    """
    needle = _normalize(answer)
    if not needle:
        return False

    # slot-fill: the constant value IS the answer (template substitution). The
    # normalized answer must EQUAL a constant value — never merely contain it as a
    # substring (which would let fabricated content ride alongside one correct
    # irreducible, or a short value collide inside a larger word).
    const_rows = conn.execute(
        text("SELECT value FROM autocm_kb_constants WHERE client_id = :c"),
        {"c": client_id},
    ).fetchall()
    for r in const_rows:
        cval = _normalize(r[0])
        if cval and cval == needle:
            return True

    # exact KB match: the answer is a substring of a surfaced ACTIVE chunk.
    ids = [int(c) for c in available_chunk_ids]
    if ids:
        chunk_rows = conn.execute(
            text(
                "SELECT chunk_text FROM autocm_kb_chunks "
                "WHERE client_id = :c AND status = 'active' AND id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"c": client_id, "ids": ids},
        ).fetchall()
        for r in chunk_rows:
            hay = _normalize(r[0])
            if hay and needle in hay:
                return True
    return False


def _normalize(value: Optional[str]) -> str:
    """Whitespace-collapse + case-fold for exact-match comparison."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.strip()).casefold()


def check_citations_db(
    conn: Connection,
    client_id: int,
    draft_text: str,
    cited_chunk_ids: Sequence[int],
    available_chunk_ids: Sequence[int],
    *,
    tier: str,
) -> CitationVerdict:
    """DB-backed tiered citation check (all three tiers).

    The full SAFETY §2.5 gate over a live ``Connection``:

      * **loose** — always passes (no check);
      * **citation-required** — the draft must carry ≥1 citation and every cited
        id must be a real ACTIVE chunk for this client
        (``autocm_kb_chunks.id`` ∩ surfaced set); else AUTO-REJECT;
      * **exact-match-or-slot-fill** — the answer must be a literal
        ``autocm_kb_constants`` value OR an exact substring of a surfaced ACTIVE
        chunk; any deviation AUTO-REJECTS.

    Unlike :func:`check_citations`, the citation-required tier here validates ids
    against the DB (not just an in-memory ``available_chunk_ids`` list), so a draft
    citing a stale / wrong / cross-client chunk id is rejected even if the id
    happened to appear in the surfaced list.
    """
    cited = list(cited_chunk_ids) if cited_chunk_ids else parse_inline_citations(draft_text)

    if tier == TIER_LOOSE:
        return CitationVerdict(
            passed=True, tier=TIER_LOOSE, reason=REASON_LOOSE_OK, cited_chunk_ids=cited
        )

    if tier == TIER_EXACT_MATCH:
        if _exact_match_in_corpus(conn, client_id, draft_text, available_chunk_ids):
            return CitationVerdict(
                passed=True,
                tier=TIER_EXACT_MATCH,
                reason=REASON_EXACT_OK,
                cited_chunk_ids=cited,
            )
        return CitationVerdict(
            passed=False,
            tier=TIER_EXACT_MATCH,
            reason=REASON_EXACT_DEVIATION,
            cited_chunk_ids=cited,
        )

    # citation-required (and the safe default for an unmapped tier).
    if not cited:
        return CitationVerdict(
            passed=False,
            tier=TIER_CITATION_REQUIRED,
            reason=REASON_NO_CITATIONS,
            cited_chunk_ids=[],
        )
    valid = _valid_chunk_ids_for_client(conn, client_id, cited)
    invalid = [c for c in cited if int(c) not in valid]
    if invalid:
        return CitationVerdict(
            passed=False,
            tier=TIER_CITATION_REQUIRED,
            reason=REASON_INVALID_CITATIONS,
            cited_chunk_ids=cited,
            invalid_ids=invalid,
        )
    return CitationVerdict(
        passed=True,
        tier=TIER_CITATION_REQUIRED,
        reason=REASON_CITED_OK,
        cited_chunk_ids=cited,
    )


__all__ = [
    # tiers
    "TIER_LOOSE",
    "TIER_CITATION_REQUIRED",
    "TIER_EXACT_MATCH",
    "CITATION_TIERS",
    "DEFAULT_TIER",
    "tier_for_category",
    # parsing
    "parse_inline_citations",
    # verdict + reason codes
    "CitationVerdict",
    "REASON_LOOSE_OK",
    "REASON_CITED_OK",
    "REASON_NO_CITATIONS",
    "REASON_INVALID_CITATIONS",
    "REASON_EXACT_OK",
    "REASON_EXACT_DEVIATION",
    # checks
    "check_citations",
    "check_citations_db",
]
