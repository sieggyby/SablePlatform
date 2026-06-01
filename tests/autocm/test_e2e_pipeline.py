"""C3.10 — end-to-end AutoCM pipeline (the happy-path drive).

Drives ONE inbound message through the full DESIGN §4 pipeline for a single
client:

    KB retrieval → classifier → drafter → gate → publisher

The load-bearing C3.10 assertion: **the deterministic surface carries the output
even when the LLM is the Null / fake provider.** With ``NullLLMProvider`` the
classifier falls back to tier-2 + calm (HITL) and the drafter falls back to the
vendored deterministic NULO render — yet the message still flows all the way to an
enqueued ``relay_publication_jobs`` outbox row (after the HITL approve), proving
the LLM is garnish (D-1 / R-4), never the hot path.

A second variant injects a deterministic FAKE provider (recorded completions, NO
network) so the LLM-driven branch is exercised too — the classifier parses real
JSON and the drafter returns a recorded draft — and the same pipeline carries it
to publish.

NO real Anthropic / network: Null or in-test FAKE provider + FakeEmbeddingProvider.
"""
from __future__ import annotations

import asyncio
import json

from sqlalchemy import text

from sable_platform.autocm.classifier.filter import (
    FilterDecision,
    PreFilterAction,
    PreFilterContext,
    prefilter,
)
from sable_platform.autocm.classifier.tier import ClassifyRequest, TierClassifier
from sable_platform.autocm.drafter.dispatch import BimodalDrafter
from sable_platform.autocm.drafter.persona import DraftRequest, NuloPersona
from sable_platform.autocm.gate.citation_check import (
    check_citations_db,
    tier_for_category,
)
from sable_platform.autocm.gate.confidence import HITL, decide
from sable_platform.autocm.gate.review_queue import record_review_decision
from sable_platform.autocm.gate.safety import check_safety
from sable_platform.autocm.kb.store import FakeEmbeddingProvider, SQLiteKBStore
from sable_platform.autocm.llm import NullLLMProvider
from sable_platform.autocm.loaders import load_client_config
from sable_platform.autocm.publisher.tg import publish_approved_draft
from sable_platform.relay import db as relay_db

from tests.autocm._c310_seed import seed_full_client


# ---------------------------------------------------------------------------
# A deterministic FAKE LLMProvider (recorded completions — NO network).
# ---------------------------------------------------------------------------
class FakeLLMProvider:
    """Returns a recorded completion per call; satisfies the core LLMProvider seam.

    ``script`` is a list of completions returned in order; once exhausted it
    returns the last one. Deterministic + offline — no anthropic import.
    """

    def __init__(self, script):
        self._script = list(script)
        self._i = 0
        self.calls = []

    async def complete(self, system, prompt, *, max_tokens=256, model=None, stop=None):
        self.calls.append((system, prompt))
        if not self._script:
            return None
        out = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return out


def _make_kb_client(conn):
    """Seed one fully-wired RobotMoney-style client and return its handles."""
    return seed_full_client(
        conn,
        org_id="orgRM",
        display_name="RobotMoney",
        calm_prompt="calm NULO system block for RobotMoney",
        reactive_prompt="reactive NULO system block for RobotMoney",
        kb_bodies=[
            "the robotmoney vault deploys treasury capital into vetted on-chain strategies",
            "vault buyback cadence is weekly and reported on chain via the dashboard",
        ],
        constants={"contract_address": "0xC0FFEE", "audit_url": "https://audit.example/rm"},
        inbound_text="how does the vault actually work?",
    )


# ===========================================================================
# (1) The deterministic surface carries even when the LLM is the NULL provider.
# ===========================================================================
def test_e2e_pipeline_carries_on_null_provider(sa_conn):
    sc = _make_kb_client(sa_conn)
    provider = NullLLMProvider()  # the LLM is OFF — deterministic surface only

    # -- load the per-client config (persona + kb scope) --------------------
    cfg = load_client_config(sa_conn, sc.org_id)
    assert cfg is not None and cfg.id == sc.client_id
    persona = NuloPersona.from_spec(cfg.persona)

    inbound = "how does the vault actually work?"

    # -- STAGE 0: stateful pre-filter (zero LLM) ----------------------------
    ctx = PreFilterContext(
        client_id=sc.client_id,
        org_id=sc.org_id,
        chat_row_id=sc.chat_row_id,
        is_reply_to_bot=True,
        external_user_id="curious_degen",
    )
    pre = prefilter(sa_conn, inbound, ctx)
    assert pre.action == PreFilterAction.PROCEED
    assert pre.engagement is not None
    assert pre.engagement.decision in FilterDecision.ALL

    # -- STAGE 1: classifier (Null provider → HITL fallback, tier-2 calm) ----
    classification = asyncio.run(
        TierClassifier(provider).classify(
            ClassifyRequest(message=inbound, client_display_name=cfg.display_name or "RobotMoney")
        )
    )
    # the Null provider returns None → the classifier's CLASSIFIER §6 HITL fallback.
    assert classification.tier == 2
    assert classification.register == "calm"
    assert classification.confidence == 0.0

    # -- STAGE 2: KB retrieval (client-scoped) ------------------------------
    store = SQLiteKBStore(sa_conn, FakeEmbeddingProvider())
    chunks = store.search(sc.client_id, inbound, top_k=3)
    assert chunks, "KB retrieval surfaced the client's own chunks"
    assert all(c.client_id == sc.client_id for c in chunks)

    # -- STAGE 3: drafter (Null provider → deterministic vendored R-4 render) -
    drafter = BimodalDrafter(persona, provider)
    draft = asyncio.run(
        drafter.compose(
            DraftRequest(
                client_id=sc.client_id,
                text=inbound,
                register=classification.register,
                category=classification.category,
                kb_chunks=chunks,
            )
        )
    )
    # the deterministic surface produced an in-voice line WITHOUT the LLM.
    assert draft.used_llm is False
    assert draft.text and draft.text.strip()

    # -- STAGE 4a: safety gate (deterministic; the draft is clean) ----------
    verdict = check_safety(draft.text)
    assert verdict.tripped is False

    # -- STAGE 4b: confidence gate — a fresh client is HITL-by-default -------
    gate = decide(sa_conn, sc.client_id, classification.category, classification.confidence)
    assert gate.outcome == HITL  # never auto on a brand-new client

    # -- STAGE 5: persist the draft, operator APPROVES, publisher enqueues ---
    draft_id = sa_conn.execute(
        text(
            "INSERT INTO autocm_drafts "
            "(client_id, source_message_id, source_chat_id, category, tier, register, "
            " draft_text, confidence, cited_chunk_ids, status) "
            "VALUES (:c, :sm, :sc, :cat, :tier, :reg, :dt, :conf, :cited, 'hitl_pending') "
            "RETURNING id"
        ),
        {
            "c": sc.client_id,
            "sm": sc.message_row_id,
            "sc": sc.chat_row_id,
            "cat": classification.category,
            "tier": classification.tier,
            "reg": draft.register,
            "dt": draft.text,
            "conf": classification.confidence,
            "cited": json.dumps(draft.cited_chunk_ids),
        },
    ).fetchone()[0]
    draft_id = int(draft_id)

    # operator approve (HITL) → mark approved, then publisher enqueues.
    record_review_decision(
        sa_conn,
        draft_id=draft_id,
        client_id=sc.client_id,
        reviewer="op-sieggy",
        decision="approve",
        draft_text=draft.text,
        org_id=sc.org_id,
        source_message_id=sc.message_row_id,
        cited_chunk_ids=draft.cited_chunk_ids,
        category=classification.category,
        tier=classification.tier,
        confidence=classification.confidence,
    )
    sa_conn.execute(
        text("UPDATE autocm_drafts SET status = 'approved' WHERE id = :d"), {"d": draft_id}
    )
    sa_conn.commit()

    result = publish_approved_draft(sa_conn, draft_id)

    # the message flowed end-to-end to exactly ONE outbox row carrying the
    # deterministic draft text — the LLM never ran, yet the surface published.
    assert result.enqueued is True
    assert result.org_id == sc.org_id
    carrier = relay_db.get_tweet_by_row_id(sa_conn, int(result.tweet_id))
    assert carrier is not None
    assert carrier["text"] == draft.text
    pending = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_publication_jobs WHERE state = 'pending'")
    ).fetchone()[0]
    assert pending == 1


# ===========================================================================
# (2) Same pipeline with a deterministic FAKE provider exercises the LLM branch.
# ===========================================================================
def test_e2e_pipeline_with_fake_llm_provider_branch(sa_conn):
    sc = _make_kb_client(sa_conn)
    cfg = load_client_config(sa_conn, sc.org_id)
    persona = NuloPersona.from_spec(cfg.persona)
    inbound = "how does the vault actually work?"

    # classifier sees recorded JSON → tier-1 mechanics calm; drafter sees a recorded
    # draft. (Two separate providers so each stage's script is independent.)
    classify_json = json.dumps(
        {
            "engage": True,
            "tier": 1,
            "category": "mechanics",
            "category_confidence": 0.91,
            "register": "calm",
            "reasoning": "KB-grounded mechanics question",
        }
    )
    classifier = TierClassifier(FakeLLMProvider([classify_json]))
    classification = asyncio.run(
        classifier.classify(ClassifyRequest(message=inbound))
    )
    assert classification.category == "mechanics"
    assert classification.register == "calm"
    assert classification.confidence == 0.91

    store = SQLiteKBStore(sa_conn, FakeEmbeddingProvider())
    chunks = store.search(sc.client_id, inbound, top_k=3)
    assert chunks

    draft_json = json.dumps(
        {
            "register": "calm",
            "draft": f"the vault deploys treasury into vetted strategies [{chunks[0].chunk_id}].",
            "reasoning": "grounded in the surfaced chunk",
        }
    )
    drafter = BimodalDrafter(persona, FakeLLMProvider([draft_json]))
    draft = asyncio.run(
        drafter.compose(
            DraftRequest(
                client_id=sc.client_id,
                text=inbound,
                register=classification.register,
                category=classification.category,
                kb_chunks=chunks,
            )
        )
    )
    # the FAKE LLM draft was used (not the deterministic fallback).
    assert draft.used_llm is True
    assert "deploys treasury" in draft.text

    # citation gate: mechanics is citation-required; the draft cites a real,
    # surfaced, client-scoped chunk → it passes.
    citation = check_citations_db(
        sa_conn,
        sc.client_id,
        draft.text,
        draft.cited_chunk_ids,
        [c.chunk_id for c in chunks],
        tier=tier_for_category(classification.category),
    )
    assert citation.passed is True

    # safety clean; then publish the approved draft end-to-end.
    assert check_safety(draft.text).tripped is False
    draft_id = sa_conn.execute(
        text(
            "INSERT INTO autocm_drafts "
            "(client_id, source_message_id, source_chat_id, category, tier, register, "
            " draft_text, confidence, cited_chunk_ids, status) "
            "VALUES (:c, :sm, :sc, :cat, :tier, :reg, :dt, :conf, :cited, 'approved') "
            "RETURNING id"
        ),
        {
            "c": sc.client_id,
            "sm": sc.message_row_id,
            "sc": sc.chat_row_id,
            "cat": classification.category,
            "tier": classification.tier,
            "reg": draft.register,
            "dt": draft.text,
            "conf": classification.confidence,
            "cited": json.dumps(draft.cited_chunk_ids),
        },
    ).fetchone()[0]
    sa_conn.commit()

    result = publish_approved_draft(sa_conn, int(draft_id))
    assert result.enqueued is True
    carrier = relay_db.get_tweet_by_row_id(sa_conn, int(result.tweet_id))
    assert carrier["text"] == draft.text
