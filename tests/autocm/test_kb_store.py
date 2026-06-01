"""C3.2a — kb.store tests.

Exit-criterion coverage (MEGAPLAN C3.2a exit/audit):
  * round-trips a chunk through embed → index → top-K retrieval (app-side cosine);
  * HYBRID retrieval is wired: an exact-term query that cosine ranks poorly is
    surfaced via the FTS5/keyword leg and appears in the fused top-K (where
    vector-only would miss it);
  * the embedding-provider adapter is config-driven (manifest-selected, default
    provider named), produces chunk/query vectors deterministically at the pinned
    ~512/64 chunk params, and its spend is logged to ``cost_events`` with the
    ``autocm.embed`` call_type;
  * embedding storage matches D-2 (TEXT JSON vector).

All offline — FakeEmbeddingProvider, NO real embedding API call.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from sable_platform.autocm.kb.store import (
    CHUNK_OVERLAP,
    CHUNK_TOKENS,
    DEFAULT_EMBEDDING_PROVIDER,
    EMBED_CALL_TYPE,
    AnthropicEmbeddingProvider,
    EmbeddingProvider,
    FakeEmbeddingProvider,
    SQLiteKBStore,
    build_embedding_provider,
    chunk_text,
    cosine,
    decode_embedding,
    encode_embedding,
)


# ---------------------------------------------------------------------------
# seed helpers
# ---------------------------------------------------------------------------
def _seed_client(conn, org_id: str) -> int:
    conn.execute(
        text(
            "INSERT INTO autocm_clients (org_id, autonomy_state, enabled) "
            "VALUES (:o, 'hitl', 1)"
        ),
        {"o": org_id},
    )
    return conn.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = :o"), {"o": org_id}
    ).fetchone()[0]


def _seed_source(conn, client_id: int, *, source_type: str = "doc", authority: float = 0.8) -> int:
    row = conn.execute(
        text(
            "INSERT INTO autocm_kb_sources (client_id, source_type, authority_default) "
            "VALUES (:c, :st, :a) RETURNING id"
        ),
        {"c": client_id, "st": source_type, "a": authority},
    ).fetchone()
    return int(row[0])


@pytest.fixture
def kb_env(sa_org):
    """(conn, org_id, client_id, source_id) with a seeded autocm client + source."""
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    source_id = _seed_source(conn, client_id)
    conn.commit()
    return conn, org_id, client_id, source_id


# ---------------------------------------------------------------------------
# embedding seam (config-driven; default named; deterministic FAKE)
# ---------------------------------------------------------------------------
def test_embedding_provider_is_config_driven_default_named() -> None:
    # The v1 default provider is NAMED (KB_DESIGN §2). Default build is the real
    # adapter stub (production), NOT the test fake.
    assert DEFAULT_EMBEDDING_PROVIDER == "voyage"
    default = build_embedding_provider()
    assert isinstance(default, AnthropicEmbeddingProvider)
    # the fake is explicitly selectable for tests
    fake = build_embedding_provider("fake")
    assert isinstance(fake, FakeEmbeddingProvider)
    # both satisfy the runtime-checkable seam protocol
    assert isinstance(default, EmbeddingProvider)
    assert isinstance(fake, EmbeddingProvider)


def test_embedding_provider_unknown_raises() -> None:
    with pytest.raises(ValueError):
        build_embedding_provider("totally-not-a-provider")


def test_fake_embedder_is_deterministic() -> None:
    fake = FakeEmbeddingProvider()
    a = fake.embed(["the vault holds the treasury"])[0]
    b = fake.embed(["the vault holds the treasury"])[0]
    assert a == b  # same text → same vector, every run
    assert len(a) == fake.dim


def test_fake_embedder_lexical_overlap_raises_cosine() -> None:
    fake = FakeEmbeddingProvider()
    q = fake.embed(["how does the vault buyback work"])[0]
    near = fake.embed(["the vault buyback mechanism explained"])[0]
    far = fake.embed(["unrelated cooking recipe banana bread"])[0]
    assert cosine(q, near) > cosine(q, far)


def test_real_adapter_stub_does_not_embed_in_tests() -> None:
    # The real adapter is a deployment-time seam stub; it must never be invoked in
    # tests (NO real embedding API). Constructing it is fine; embed() raises.
    stub = AnthropicEmbeddingProvider()
    with pytest.raises(NotImplementedError):
        stub.embed(["x"])


# ---------------------------------------------------------------------------
# chunking (pinned ~512/64 — KB_DESIGN §10)
# ---------------------------------------------------------------------------
def test_chunk_params_pinned() -> None:
    assert CHUNK_TOKENS == 512
    assert CHUNK_OVERLAP == 64


def test_chunk_short_body_single_chunk() -> None:
    assert chunk_text("short body here") == ["short body here"]


def test_chunk_long_body_overlaps_deterministically() -> None:
    tokens = [f"t{i}" for i in range(CHUNK_TOKENS + 100)]
    body = " ".join(tokens)
    chunks = chunk_text(body)
    assert len(chunks) >= 2
    first = chunks[0].split()
    second = chunks[1].split()
    assert len(first) == CHUNK_TOKENS
    # consecutive windows overlap by exactly CHUNK_OVERLAP tokens
    step = CHUNK_TOKENS - CHUNK_OVERLAP
    assert first[step:] == second[: CHUNK_OVERLAP]
    # deterministic: same input → same chunks
    assert chunk_text(body) == chunks


# ---------------------------------------------------------------------------
# D-2 embedding storage (TEXT JSON vector)
# ---------------------------------------------------------------------------
def test_embedding_storage_is_text_json_d2(kb_env) -> None:
    conn, org_id, client_id, source_id = kb_env
    store = SQLiteKBStore(conn, FakeEmbeddingProvider())
    ids = store.index_source(
        org_id=org_id, client_id=client_id, source_id=source_id,
        body="the robotmoney vault tvl is reported on chain",
        source_type="doc", authority=0.8,
    )
    conn.commit()
    assert len(ids) == 1
    raw = conn.execute(
        text("SELECT chunk_embedding FROM autocm_kb_chunks WHERE id = :id"),
        {"id": ids[0]},
    ).fetchone()[0]
    # stored as TEXT, decodes to a float vector (D-2; NO BLOB extension)
    assert isinstance(raw, str)
    vec = decode_embedding(raw)
    assert vec is not None and len(vec) == FakeEmbeddingProvider().dim
    # round-trip encode/decode is stable
    assert decode_embedding(encode_embedding(vec)) == pytest.approx(vec)


# ---------------------------------------------------------------------------
# round-trip: embed → index → top-K (app-side cosine)
# ---------------------------------------------------------------------------
def test_round_trip_cosine_top_k_ordering(kb_env) -> None:
    conn, org_id, client_id, source_id = kb_env
    store = SQLiteKBStore(conn, FakeEmbeddingProvider())
    store.index_source(
        org_id=org_id, client_id=client_id, source_id=source_id,
        body="the vault buyback mechanism deploys treasury capital", source_type="doc",
    )
    store.index_source(
        org_id=org_id, client_id=client_id, source_id=source_id,
        body="our discord community guidelines and moderation rules", source_type="doc",
    )
    store.index_source(
        org_id=org_id, client_id=client_id, source_id=source_id,
        body="banana bread recipe with walnuts and cinnamon", source_type="doc",
    )
    conn.commit()

    results = store.search(client_id, "how does the vault buyback work", top_k=3)
    assert results, "expected hits"
    # the lexically/semantically closest chunk (vault buyback) ranks first
    assert "buyback" in results[0].text
    # retrieved KBChunks carry the citation metadata the C3.5a gate needs
    assert results[0].source_type == "doc"
    assert 0.0 <= results[0].authority <= 1.0
    assert results[0].score > 0.0


# ---------------------------------------------------------------------------
# HYBRID: FTS5 keyword leg surfaces a chunk cosine would miss
# ---------------------------------------------------------------------------
def test_hybrid_keyword_leg_surfaces_exact_term_cosine_misses(kb_env) -> None:
    conn, org_id, client_id, source_id = kb_env
    store = SQLiteKBStore(conn, FakeEmbeddingProvider())
    # Target: bears the rare exact term but shares minimal generic vocabulary with
    # the query, so the bag-of-tokens cosine ranks it LAST.
    store.index_source(
        org_id=org_id, client_id=client_id, source_id=source_id,
        body="the redeemUnderlying function withdraws collateral from the lending pool position",
        source_type="contract",
    )
    # Noise chunks heavily repeat the query's GENERIC words ("vault buyback how
    # works") so they dominate cosine and crowd the rare-term chunk out of a narrow
    # cosine-only top-K — but none contains the rare exact term.
    for body in (
        "vault buyback vault buyback how the vault buyback works in our vault buyback flow",
        "how the vault buyback how vault buyback how the vault buyback how how vault",
        "vault vault vault buyback buyback buyback how how how the the the works",
        "vault buyback how works vault buyback how works vault buyback how works today",
    ):
        store.index_source(
            org_id=org_id, client_id=client_id, source_id=source_id, body=body, source_type="doc",
        )
    conn.commit()

    query = "how does redeemUnderlying affect the vault buyback"
    target_id = conn.execute(
        text("SELECT id FROM autocm_kb_chunks WHERE chunk_text LIKE '%redeemUnderlying%'")
    ).fetchone()[0]

    # VECTOR-ONLY (narrow top-3) MISSES the rare-term chunk — cosine ranks it last.
    cosine_only = store._cosine_leg(client_id, query, leg_n=3)
    assert target_id not in cosine_only, "fixture must demonstrate cosine missing it"

    # The FTS5/BM25 keyword leg ranks the rare exact term highly...
    keyword_ids = store._keyword_leg(client_id, query, leg_n=10)
    assert target_id in keyword_ids

    # ...so the HYBRID fused top-3 SURFACES it where vector-only would not.
    fused_ids = [c.chunk_id for c in store.search(client_id, query, top_k=3)]
    assert target_id in fused_ids, "FTS5 keyword leg must surface the exact-term chunk"


# ---------------------------------------------------------------------------
# embedding spend logged to cost_events (autocm.embed call_type)
# ---------------------------------------------------------------------------
def test_embed_spend_logged_to_cost_events(kb_env) -> None:
    conn, org_id, client_id, source_id = kb_env
    store = SQLiteKBStore(conn, FakeEmbeddingProvider())
    store.index_source(
        org_id=org_id, client_id=client_id, source_id=source_id,
        body="the vault tvl and buyback cadence", source_type="doc",
    )
    conn.commit()
    rows = conn.execute(
        text(
            "SELECT call_type, org_id, model, input_tokens FROM cost_events "
            "WHERE call_type = :ct AND org_id = :o"
        ),
        {"ct": EMBED_CALL_TYPE, "o": org_id},
    ).fetchall()
    assert rows, "embedding spend must be logged"
    assert rows[0][0] == "autocm.embed"
    assert rows[0][1] == org_id
    assert rows[0][2] == "fake"  # the FakeEmbeddingProvider name
    assert rows[0][3] > 0  # input tokens accounted


# ---------------------------------------------------------------------------
# per-client isolation (KB_DESIGN §6) — no cross-client retrieval
# ---------------------------------------------------------------------------
def test_search_is_per_client_isolated(sa_org) -> None:
    conn, org_id = sa_org
    # a second org/client in the SAME db
    conn.execute(
        text("INSERT INTO orgs (org_id, display_name) VALUES ('org2', 'Org Two')")
    )
    client_a = _seed_client(conn, org_id)
    client_b = _seed_client(conn, "org2")
    source_a = _seed_source(conn, client_a)
    source_b = _seed_source(conn, client_b)
    conn.commit()

    store = SQLiteKBStore(conn, FakeEmbeddingProvider())
    store.index_source(
        org_id=org_id, client_id=client_a, source_id=source_a,
        body="client A secret vault buyback details", source_type="doc",
    )
    store.index_source(
        org_id="org2", client_id=client_b, source_id=source_b,
        body="client B secret vault buyback details", source_type="doc",
    )
    conn.commit()

    res_a = store.search(client_a, "vault buyback", top_k=5)
    assert res_a and all(c.client_id == client_a for c in res_a)
    assert all("client A" in c.text for c in res_a)
    res_b = store.search(client_b, "vault buyback", top_k=5)
    assert res_b and all(c.client_id == client_b for c in res_b)
