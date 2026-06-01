"""C3.5a — tiered citation / hallucination gate (SAFETY §2.5).

Three tiers: loose (no programmatic check), citation-required (inline [chunk_id]
refs validated against autocm_kb_chunks.id; missing/invalid → AUTO-REJECT),
exact-match-or-slot-fill (literal slot-fill from autocm_kb_constants OR exact KB
match; any deviation → AUTO-REJECT).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from sable_platform.autocm.gate.citation_check import (
    REASON_EXACT_DEVIATION,
    REASON_EXACT_OK,
    REASON_INVALID_CITATIONS,
    REASON_NO_CITATIONS,
    TIER_CITATION_REQUIRED,
    TIER_EXACT_MATCH,
    TIER_LOOSE,
    check_citations,
    check_citations_db,
    parse_inline_citations,
    tier_for_category,
)


def _seed_client(conn, org_id):
    conn.execute(
        text(
            "INSERT INTO autocm_clients (org_id, display_name, autonomy_state, enabled) "
            "VALUES (:o, 'RM', 'hitl', 1)"
        ),
        {"o": org_id},
    )
    return conn.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = :o"), {"o": org_id}
    ).fetchone()[0]


def _seed_source(conn, client_id):
    conn.execute(
        text(
            "INSERT INTO autocm_kb_sources (client_id, source_type, authority_default) "
            "VALUES (:c, 'doc', 0.8)"
        ),
        {"c": client_id},
    )
    return conn.execute(
        text("SELECT id FROM autocm_kb_sources ORDER BY id DESC LIMIT 1")
    ).fetchone()[0]


def _seed_chunk(conn, client_id, source_id, chunk_text, *, status="active"):
    conn.execute(
        text(
            "INSERT INTO autocm_kb_chunks "
            "(source_id, client_id, chunk_text, status) "
            "VALUES (:s, :c, :t, :st)"
        ),
        {"s": source_id, "c": client_id, "t": chunk_text, "st": status},
    )
    return conn.execute(
        text("SELECT id FROM autocm_kb_chunks ORDER BY id DESC LIMIT 1")
    ).fetchone()[0]


def _seed_constant(conn, client_id, key, value):
    conn.execute(
        text(
            "INSERT INTO autocm_kb_constants (client_id, key, value) "
            "VALUES (:c, :k, :v)"
        ),
        {"c": client_id, "k": key, "v": value},
    )


# ---------------------------------------------------------------------------
# tier mapping + inline parsing
# ---------------------------------------------------------------------------
def test_tier_for_category():
    assert tier_for_category("greeting") == TIER_LOOSE
    assert tier_for_category("mechanics") == TIER_CITATION_REQUIRED
    assert tier_for_category("trust") == TIER_EXACT_MATCH
    # unknown / None → safe default citation-required
    assert tier_for_category("zzz") == TIER_CITATION_REQUIRED
    assert tier_for_category(None) == TIER_CITATION_REQUIRED


def test_tier_for_category_sentiment_and_fud_are_citation_required():
    """SAFETY §2.5 (docs/SAFETY.md:72) lists sentiment_negative + FUD_borderline
    under the CITATION-REQUIRED row, NOT loose. Pinned so the §2.5 placement of
    these two auto-eligible tier-2 categories cannot silently drift back to loose
    (which would fail the tiered-hallucination gate OPEN on a fabricated fact in an
    auto-sent FUD rebuttal / angry-holder reassurance — the §2.5 worst failure mode).
    """
    assert tier_for_category("sentiment_negative") == TIER_CITATION_REQUIRED
    assert tier_for_category("FUD_borderline") == TIER_CITATION_REQUIRED
    # neither may be loose.
    assert tier_for_category("sentiment_negative") != TIER_LOOSE
    assert tier_for_category("FUD_borderline") != TIER_LOOSE


def test_parse_inline_citations():
    assert parse_inline_citations("answer [12] and [ 34 ] and again [12]") == [12, 34]
    assert parse_inline_citations("no refs here") == []
    assert parse_inline_citations("") == []


# ---------------------------------------------------------------------------
# loose tier — always passes
# ---------------------------------------------------------------------------
def test_loose_tier_always_passes():
    v = check_citations("gm frens", [], [], tier=TIER_LOOSE)
    assert v.passed is True
    assert v.tier == TIER_LOOSE


def test_loose_tier_db_always_passes(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    conn.commit()
    v = check_citations_db(conn, client_id, "gm", [], [], tier=TIER_LOOSE)
    assert v.passed is True


# ---------------------------------------------------------------------------
# citation-required tier (in-memory available set)
# ---------------------------------------------------------------------------
def test_citation_required_no_citation_auto_rejects():
    v = check_citations("the vault deploys capital", [], [1, 2, 3], tier=TIER_CITATION_REQUIRED)
    assert v.passed is False
    assert v.reason == REASON_NO_CITATIONS


def test_citation_required_invalid_id_auto_rejects():
    # cites [99] but only 1,2,3 were surfaced.
    v = check_citations("the vault deploys capital [99]", [], [1, 2, 3], tier=TIER_CITATION_REQUIRED)
    assert v.passed is False
    assert v.reason == REASON_INVALID_CITATIONS
    assert v.invalid_ids == [99]


def test_citation_required_valid_citation_passes():
    v = check_citations("the vault deploys capital [2]", [], [1, 2, 3], tier=TIER_CITATION_REQUIRED)
    assert v.passed is True
    assert v.cited_chunk_ids == [2]


def test_citation_required_uses_structured_list_over_inline():
    # explicit cited_chunk_ids wins over inline parse.
    v = check_citations("text without brackets", [2], [1, 2, 3], tier=TIER_CITATION_REQUIRED)
    assert v.passed is True
    assert v.cited_chunk_ids == [2]


def test_check_citations_refuses_exact_match_tier():
    """SAFETY §2.5 (high fix): the non-DB check_citations() must REFUSE the
    exact-match tier rather than silently downgrade it to citation-required while
    mislabeling the verdict ``tier='exact_match_or_slot_fill'``. Previously
    check_citations("the contract is 0xDEADBEEF [2]", [], [1,2,3],
    tier=TIER_EXACT_MATCH) returned passed=True (a fabricated irreducible carrying
    one valid [chunk_id] auto-APPROVED, fail-open). It must raise so the highest-
    stakes tier can only be decided by the DB path (check_citations_db).
    """
    with pytest.raises(ValueError, match="exact-match tier requires a Connection"):
        check_citations(
            "the contract is 0xDEADBEEF [2]", [], [1, 2, 3], tier=TIER_EXACT_MATCH
        )


# ---------------------------------------------------------------------------
# citation-required tier (DB-validated)
# ---------------------------------------------------------------------------
def test_citation_required_db_validates_against_active_chunks(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    src = _seed_source(conn, client_id)
    good = _seed_chunk(conn, client_id, src, "the vault deploys treasury capital")
    conn.commit()
    v = check_citations_db(
        conn, client_id, f"answer [{good}]", [], [good], tier=TIER_CITATION_REQUIRED
    )
    assert v.passed is True


def test_citation_required_db_rejects_stale_chunk(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    src = _seed_source(conn, client_id)
    stale = _seed_chunk(conn, client_id, src, "old fact", status="stale")
    conn.commit()
    # the id appears in available_chunk_ids but the chunk is stale → not a valid target.
    v = check_citations_db(
        conn, client_id, f"answer [{stale}]", [], [stale], tier=TIER_CITATION_REQUIRED
    )
    assert v.passed is False
    assert v.reason == REASON_INVALID_CITATIONS
    assert v.invalid_ids == [stale]


def test_citation_required_db_rejects_cross_client_chunk(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    # a second client's chunk
    conn.execute(
        text("INSERT INTO orgs (org_id, display_name) VALUES ('other', 'Other')")
    )
    other_id = _seed_client(conn, "other")
    src = _seed_source(conn, other_id)
    other_chunk = _seed_chunk(conn, other_id, src, "other client fact")
    conn.commit()
    v = check_citations_db(
        conn, client_id, f"answer [{other_chunk}]", [], [other_chunk],
        tier=TIER_CITATION_REQUIRED,
    )
    assert v.passed is False
    assert v.invalid_ids == [other_chunk]


# ---------------------------------------------------------------------------
# exact-match-or-slot-fill tier
# ---------------------------------------------------------------------------
def test_exact_match_passes_on_slotfill_constant(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_constant(conn, client_id, "contract_address", "0x6502aBC1234dEf")
    conn.commit()
    # SAFETY §2.5 slot-fill is literal template substitution: the rendered answer
    # IS the irreducible value (`Contract: {{contract_address}}` → the value).
    # The normalized answer must EQUAL the constant, not merely contain it.
    v = check_citations_db(
        conn, client_id, "0x6502aBC1234dEf", [], [], tier=TIER_EXACT_MATCH
    )
    assert v.passed is True
    assert v.reason == REASON_EXACT_OK


def test_exact_match_slotfill_rejects_fabrication_alongside_irreducible(sa_org):
    """SAFETY §2.5 (medium fix): the slot-fill leg must NOT clear a draft that
    merely CONTAINS a correct irreducible while a fabricated high-stakes claim rides
    alongside. Under the old loose ``cval in needle`` substring direction this draft
    passed (the contract value is a substring); under whole-answer EQUAL it does not
    — the value is not the whole answer, and the fabrication AUTO-REJECTS.
    """
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_constant(conn, client_id, "contract_address", "0x6502aBC1234dEf")
    conn.commit()
    v = check_citations_db(
        conn,
        client_id,
        "the contract is 0x6502aBC1234dEf and the audit was by FakeAuditor "
        "with 9000% APY guaranteed",
        [],
        [],
        tier=TIER_EXACT_MATCH,
    )
    assert v.passed is False
    assert v.reason == REASON_EXACT_DEVIATION


def test_exact_match_slotfill_rejects_short_constant_substring_collision(sa_org):
    """SAFETY §2.5 (medium fix): a short constant (a ticker / handle) must not clear
    the tier as an incidental substring of a larger word. Old loose direction:
    ticker 'RM' is a substring of 'rmbling' → passed. Whole-answer EQUAL: 'rm' is
    not the whole normalized answer → AUTO-REJECT.
    """
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_constant(conn, client_id, "ticker", "RM")
    conn.commit()
    v = check_citations_db(
        conn, client_id, "i am rmbling about random things", [], [], tier=TIER_EXACT_MATCH
    )
    assert v.passed is False
    assert v.reason == REASON_EXACT_DEVIATION


def test_exact_match_rejects_transposed_contract_address(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_constant(conn, client_id, "contract_address", "0x6502aBC1234dEf")
    conn.commit()
    # one transposed character → not the literal value → AUTO-REJECT.
    v = check_citations_db(
        conn, client_id, "the contract is 0x6502aCB1234dEf", [], [], tier=TIER_EXACT_MATCH
    )
    assert v.passed is False
    assert v.reason == REASON_EXACT_DEVIATION


def test_exact_match_passes_on_exact_kb_substring(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    src = _seed_source(conn, client_id)
    chunk = _seed_chunk(conn, client_id, src, "The audit was completed by Zellic in March 2026.")
    conn.commit()
    v = check_citations_db(
        conn, client_id, "the audit was completed by Zellic", [], [chunk],
        tier=TIER_EXACT_MATCH,
    )
    assert v.passed is True


def test_exact_match_rejects_paraphrase(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    src = _seed_source(conn, client_id)
    chunk = _seed_chunk(conn, client_id, src, "The audit was completed by Zellic in March 2026.")
    conn.commit()
    # a paraphrase is not an exact substring → AUTO-REJECT.
    v = check_citations_db(
        conn, client_id, "Zellic finished auditing us last spring", [], [chunk],
        tier=TIER_EXACT_MATCH,
    )
    assert v.passed is False
    assert v.reason == REASON_EXACT_DEVIATION


def test_exact_match_whitespace_and_case_insensitive(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_constant(conn, client_id, "official_twitter", "@RobotMoney")
    conn.commit()
    # different casing / surrounding whitespace is not a deviation of the irreducible:
    # the slot-fill answer IS the value (template substitution), normalized to equal
    # the constant regardless of case / leading-trailing whitespace.
    v = check_citations_db(
        conn, client_id, "  @robotmoney  ", [], [],
        tier=TIER_EXACT_MATCH,
    )
    assert v.passed is True
