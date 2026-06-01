"""C3.10 — per-client isolation (NO KB / persona / config / draft bleed).

The **hard C3.10 exit** (MEGAPLAN C3.10 exit/audit): "per-client isolation (no
KB/persona/config bleed)". Two clients (A and B) are seeded with DISTINCT personas,
KB chunks, slot-fill constants, category state, and drafts in the SAME in-memory
db; every read/decision for client A is asserted to NEVER read client B's rows, and
vice-versa. Each query in the pipeline is ``client_id``-scoped:

  * KB retrieval (``SQLiteKBStore.search``) — A never surfaces B's chunks.
  * persona load (``load_client_config`` → ``NuloPersona.from_spec``) — A's prompts.
  * slot-fill constants (``ConstantsKB``) — A's constant value, never B's.
  * category state (``resolve_category_state`` / ``decide``) — promoting A's
    category to ``auto`` does NOT make B's same-named category ``auto``.
  * citation gate (``check_citations_db``) — A citing B's chunk id is REJECTED.
  * exact-match slot-fill (``check_citations_db`` exact tier) — A's answer that
    equals B's secret constant value is REJECTED for A.
  * publisher (``publish_approved_draft``) — publishing A's draft never touches B's,
    and the destination resolves to A's own chat.

NO real Anthropic / network: deterministic FakeEmbeddingProvider; no LLM call.
"""
from __future__ import annotations

from sqlalchemy import text

from sable_platform.autocm.classifier.categories import resolve_category_state
from sable_platform.autocm.gate.autonomy import promote_category
from sable_platform.autocm.gate.citation_check import (
    TIER_CITATION_REQUIRED,
    TIER_EXACT_MATCH,
    check_citations_db,
)
from sable_platform.autocm.gate.confidence import AUTO, HITL, decide
from sable_platform.autocm.kb.constants import ConstantsKB
from sable_platform.autocm.kb.store import FakeEmbeddingProvider, SQLiteKBStore
from sable_platform.autocm.loaders import load_client_config
from sable_platform.autocm.drafter.persona import NuloPersona
from sable_platform.autocm.publisher.tg import publish_approved_draft

from tests.autocm._c310_seed import insert_draft, seed_full_client


# ---------------------------------------------------------------------------
# Two distinct tenants in ONE db: A = RobotMoney, B = AcmeDAO.
# ---------------------------------------------------------------------------
def _seed_two_clients(conn):
    a = seed_full_client(
        conn,
        org_id="orgA",
        display_name="RobotMoney",
        calm_prompt="CALM-A robotmoney persona block",
        reactive_prompt="REACTIVE-A robotmoney persona block",
        kb_bodies=[
            "client A secret vault buyback deploys treasury capital weekly",
            "client A on chain dashboard reports the vault tvl",
        ],
        constants={"contract_address": "0xAAAA1111", "audit_url": "https://audit/A"},
        inbound_text="how does the vault buyback work?",
        chat_external_id="-100AAA",
        msg_external_id="A-msg-1",
    )
    b = seed_full_client(
        conn,
        org_id="orgB",
        display_name="AcmeDAO",
        calm_prompt="CALM-B acmedao persona block",
        reactive_prompt="REACTIVE-B acmedao persona block",
        kb_bodies=[
            "client B secret staking rewards accrue per epoch to delegators",
            "client B governance forum hosts the proposal lifecycle",
        ],
        constants={"contract_address": "0xBBBB2222", "audit_url": "https://audit/B"},
        inbound_text="how do staking rewards work?",
        chat_external_id="-100BBB",
        msg_external_id="B-msg-1",
    )
    return a, b


# ===========================================================================
# KB retrieval isolation — A never surfaces B's chunks (both directions).
# ===========================================================================
def test_kb_retrieval_never_bleeds_across_clients(sa_conn):
    a, b = _seed_two_clients(sa_conn)
    store = SQLiteKBStore(sa_conn, FakeEmbeddingProvider())

    # A's query for a term present in BOTH corpora ("secret") must return ONLY A.
    res_a = store.search(a.client_id, "secret vault buyback dashboard tvl", top_k=10)
    assert res_a
    assert all(c.client_id == a.client_id for c in res_a)
    assert all("client A" in c.text for c in res_a)
    assert not any("client B" in c.text for c in res_a)

    res_b = store.search(b.client_id, "secret staking rewards governance", top_k=10)
    assert res_b
    assert all(c.client_id == b.client_id for c in res_b)
    assert all("client B" in c.text for c in res_b)
    assert not any("client A" in c.text for c in res_b)


# ===========================================================================
# Persona isolation — A loads A's prompts, never B's.
# ===========================================================================
def test_persona_load_is_client_scoped(sa_conn):
    a, b = _seed_two_clients(sa_conn)

    cfg_a = load_client_config(sa_conn, a.org_id)
    cfg_b = load_client_config(sa_conn, b.org_id)
    assert cfg_a is not None and cfg_b is not None
    assert cfg_a.id == a.client_id and cfg_b.id == b.client_id
    assert cfg_a.persona_id != cfg_b.persona_id

    persona_a = NuloPersona.from_spec(cfg_a.persona)
    persona_b = NuloPersona.from_spec(cfg_b.persona)
    assert "CALM-A" in persona_a.calm_prompt
    assert "CALM-A" not in persona_b.calm_prompt
    assert "CALM-B" in persona_b.calm_prompt
    assert "CALM-B" not in persona_a.calm_prompt


# ===========================================================================
# Slot-fill constants isolation — A's value, never B's.
# ===========================================================================
def test_constants_slotfill_is_client_scoped(sa_conn):
    a, b = _seed_two_clients(sa_conn)

    kb_a = ConstantsKB.load(sa_conn, a.client_id)
    kb_b = ConstantsKB.load(sa_conn, b.client_id)

    # the SAME question key resolves to DIFFERENT per-client values.
    assert kb_a.constant("contract_address") == "0xAAAA1111"
    assert kb_b.constant("contract_address") == "0xBBBB2222"
    assert kb_a.match_slotfill("what's the contract address") == (
        "contract_address",
        "0xAAAA1111",
    )
    assert kb_b.match_slotfill("what's the contract address") == (
        "contract_address",
        "0xBBBB2222",
    )
    # A never sees B's value.
    assert kb_a.constant("contract_address") != "0xBBBB2222"


# ===========================================================================
# Category-state / autonomy isolation — promoting A does not promote B.
# ===========================================================================
def test_category_state_and_promotion_do_not_bleed(sa_conn):
    a, b = _seed_two_clients(sa_conn)
    category = "greeting"  # tier-1 auto-eligible, low floor

    # Seed a clean-review history for A ONLY so A's greeting can promote.
    _seed_clean_reviews(sa_conn, a.client_id, category, n=50)

    verdict = promote_category(
        sa_conn, a.client_id, category, actor="op", operator_sign_off=True, org_id=a.org_id
    )
    assert verdict.promote is True
    sa_conn.commit()

    # A's greeting is now 'auto'; B's greeting (no rows seeded) is still HITL-default.
    state_a = resolve_category_state(sa_conn, a.client_id, category)
    state_b = resolve_category_state(sa_conn, b.client_id, category)
    assert state_a is not None and state_a.state == "auto"
    assert state_b is not None and state_b.state == "hitl"

    # The read-side gate agrees: A auto-eligible at high confidence, B forced HITL.
    gate_a = decide(sa_conn, a.client_id, category, 0.95)
    gate_b = decide(sa_conn, b.client_id, category, 0.95)
    assert gate_a.outcome == AUTO
    assert gate_b.outcome == HITL  # B never promoted — no bleed from A's promotion


def _seed_clean_reviews(conn, client_id, category, *, n):
    """Insert n clean-approval reviews on drafts in ``category`` for ``client_id``."""
    for _ in range(n):
        draft_id = insert_draft(
            conn,
            client_id=client_id,
            source_message_id=None,
            source_chat_id=None,
            draft_text="gm",
            category=category,
            tier=1,
            status="approved",
        )
        conn.execute(
            text(
                "INSERT INTO autocm_reviews "
                "(draft_id, client_id, reviewer, decision, is_clean_approval) "
                "VALUES (:d, :c, 'op', 'approve', 1)"
            ),
            {"d": draft_id, "c": client_id},
        )
    conn.commit()


# ===========================================================================
# Citation gate isolation — A citing B's chunk id is REJECTED.
# ===========================================================================
def test_citation_gate_rejects_cross_client_chunk(sa_conn):
    a, b = _seed_two_clients(sa_conn)
    b_chunk = b.chunk_ids[0]
    a_chunk = a.chunk_ids[0]

    # A draft for client A that cites client B's chunk id is rejected: B's chunk is
    # NOT a valid citation target for A (KB_DESIGN §6 isolation in the DB gate).
    bad = check_citations_db(
        sa_conn,
        a.client_id,
        draft_text=f"per the docs [{b_chunk}] the answer is X.",
        cited_chunk_ids=[b_chunk],
        available_chunk_ids=[b_chunk],  # even if it 'surfaced', it's not A's
        tier=TIER_CITATION_REQUIRED,
    )
    assert bad.passed is False
    assert b_chunk in bad.invalid_ids

    # the SAME draft citing A's OWN chunk passes — proving the rejection was the
    # cross-client guard, not a blanket failure.
    good = check_citations_db(
        sa_conn,
        a.client_id,
        draft_text=f"per the docs [{a_chunk}] the answer is X.",
        cited_chunk_ids=[a_chunk],
        available_chunk_ids=[a_chunk],
        tier=TIER_CITATION_REQUIRED,
    )
    assert good.passed is True


# ===========================================================================
# Exact-match slot-fill isolation — A answering with B's secret value is REJECTED.
# ===========================================================================
def test_exact_match_slotfill_does_not_accept_other_clients_constant(sa_conn):
    a, b = _seed_two_clients(sa_conn)

    # client A's answer is B's secret contract address. Under per-client scoping the
    # exact-match leg reads ONLY A's autocm_kb_constants, so B's value is NOT a valid
    # slot-fill for A → AUTO-REJECT (a leak of B's irreducible would otherwise pass).
    leak = check_citations_db(
        sa_conn,
        a.client_id,
        draft_text="0xBBBB2222",
        cited_chunk_ids=[],
        available_chunk_ids=[],
        tier=TIER_EXACT_MATCH,
    )
    assert leak.passed is False

    # A answering with A's OWN constant value passes (whole-answer slot-fill).
    ok = check_citations_db(
        sa_conn,
        a.client_id,
        draft_text="0xAAAA1111",
        cited_chunk_ids=[],
        available_chunk_ids=[],
        tier=TIER_EXACT_MATCH,
    )
    assert ok.passed is True


# ===========================================================================
# Publisher isolation — publishing A's draft never touches B; dest is A's chat.
# ===========================================================================
def test_publisher_is_client_scoped(sa_conn):
    a, b = _seed_two_clients(sa_conn)

    # one approved draft per client, each answering its OWN inbound message.
    draft_a = insert_draft(
        sa_conn,
        client_id=a.client_id,
        source_message_id=a.message_row_id,
        source_chat_id=a.chat_row_id,
        draft_text="A reply: the vault buyback deploys treasury weekly.",
        status="approved",
    )
    draft_b = insert_draft(
        sa_conn,
        client_id=b.client_id,
        source_message_id=b.message_row_id,
        source_chat_id=b.chat_row_id,
        draft_text="B reply: staking rewards accrue per epoch.",
        status="approved",
    )
    sa_conn.commit()

    # publish ONLY A's draft.
    result = publish_approved_draft(sa_conn, draft_a)
    assert result.enqueued is True
    assert result.org_id == a.org_id

    # exactly ONE outbox row, for A's org + A's chat — B was never touched.
    jobs = [
        dict(r._mapping)
        for r in sa_conn.execute(
            text(
                "SELECT org_id, destination_chat_id FROM relay_publication_jobs "
                "WHERE state = 'pending'"
            )
        ).fetchall()
    ]
    assert len(jobs) == 1
    assert jobs[0]["org_id"] == a.org_id
    assert jobs[0]["destination_chat_id"] == "-100AAA"

    # B's draft is still 'approved' (untouched), A's is 'published'.
    status_a = sa_conn.execute(
        text("SELECT status FROM autocm_drafts WHERE id = :d"), {"d": draft_a}
    ).fetchone()[0]
    status_b = sa_conn.execute(
        text("SELECT status FROM autocm_drafts WHERE id = :d"), {"d": draft_b}
    ).fetchone()[0]
    assert status_a == "published"
    assert status_b == "approved"
