"""KB store — chunk + embed + index + HYBRID (cosine + FTS5/BM25) retrieval (C3.2a).

DECISION D-2 (locked, 058): embeddings are stored as TEXT (a JSON-encoded float
vector) on ``autocm_kb_chunks.chunk_embedding`` — pure SQLite, NO
``enable_load_extension`` / sqlite-vss. Retrieval is HYBRID per ``KB_DESIGN §3``:

  1. **App-side cosine top-K** — every active chunk's stored vector is decoded and
     scored against the query vector in Python (the universal default + SQLite-dev
     path; pgvector is an optional Postgres accelerator gated behind an ops step,
     not implemented here).
  2. **FTS5 / BM25 keyword leg** — a lexical query over the ``autocm_kb_chunks_fts``
     companion virtual table (stdlib ``sqlite3`` FTS5 — no extension load). Surfaces
     exact-term matches (contract names, ticker symbols, function names) that
     embedding similarity ranks poorly.
  3. **Reciprocal-rank fusion** — the cosine and FTS5 ranked lists are merged with
     RRF *before* C3.2c's authority/recency weighting. A chunk that either leg ranks
     well survives into the fused top-K, so a keyword-only-matchable fact is never
     silently dropped (which would otherwise become a false citation-required
     auto-reject at the C3.5a gate).

The ``embed`` step is a config-driven :class:`EmbeddingProvider` seam (parallel to
the C3.1 ``LLMProvider`` adapter, manifest-selected) — a real-adapter stub
(:class:`AnthropicEmbeddingProvider`) plus a deterministic :class:`FakeEmbeddingProvider`
for tests (NO real embedding API in tests). Embedding spend routes through
``cost_events`` with an ``autocm.embed`` call_type.

Authority/recency weighting and the freshness-contract downgrade are C3.2c — they
run over the FUSED result set this module produces.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from sable_platform.db.cost import log_cost

logger = logging.getLogger(__name__)

# Pinned chunking params (KB_DESIGN §10) so chunking is deterministic for the
# C3.2a round-trip test. Tokenizer = whitespace-split tokens (the same family the
# C3.5a edit_diff_ratio uses) — deterministic, dependency-free, no tiktoken.
CHUNK_TOKENS = 512
CHUNK_OVERLAP = 64

# cost_events.call_type for embedding spend (KB_DESIGN §2 / C3.2a budget routing).
EMBED_CALL_TYPE = "autocm.embed"

# Reciprocal-rank-fusion damping constant (Cormack et al. 2009 default).
_RRF_K = 60

_FAKE_EMBED_DIM = 64


# ---------------------------------------------------------------------------
# Embedding-provider seam (config-driven; parallel to the C3.1 LLMProvider)
# ---------------------------------------------------------------------------
@runtime_checkable
class EmbeddingProvider(Protocol):
    """The ``embed`` step seam: text → a fixed-length float vector.

    A deployment selects the concrete provider via the manifest
    (``kb.embedding.provider``). The vector must be deterministic for a given
    provider+text so the round-trip / cosine-ordering tests are reproducible.
    """

    @property
    def name(self) -> str:
        """Provider identity (recorded on cost rows / for drift checks)."""
        ...

    @property
    def dim(self) -> int:
        """Embedding dimensionality (vectors are this long)."""
        ...

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """Embed a batch of strings → one float vector per input (same order)."""
        ...


class FakeEmbeddingProvider:
    """Deterministic, offline embedder for tests (NO network, NO API key).

    Hashes each whitespace token into a fixed-dimension bag-of-tokens vector, then
    L2-normalizes. Properties that make it a faithful cosine test double:

      * **deterministic** — same text → same vector, every run (hash-seeded).
      * **lexical-overlap ⇒ cosine similarity** — texts sharing tokens have higher
        cosine than texts that don't, so cosine top-K ordering is meaningful and
        assertable WITHOUT a real embedding model.

    This is the universal test provider; production uses
    :class:`AnthropicEmbeddingProvider` (or another configured adapter).
    """

    def __init__(self, *, dim: int = _FAKE_EMBED_DIM) -> None:
        self._dim = dim

    @property
    def name(self) -> str:
        return "fake"

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, txt: str) -> List[float]:
        vec = [0.0] * self._dim
        for tok in _tokenize(txt):
            # Stable per-token hash → bucket index + sign, independent of run/seed.
            h = hashlib.sha256(tok.encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "big") % self._dim
            sign = 1.0 if h[4] & 1 else -1.0
            vec[idx] += sign
        return _l2_normalize(vec)


class AnthropicEmbeddingProvider:
    """Real-adapter STUB for the configured production embedder (C3.2a seam).

    Per ``KB_DESIGN §2`` / ``DESIGN §4`` embeddings are produced client-side via a
    config-driven adapter; Anthropic ships NO first-party embedding model, so the
    concrete v1 default is a real per-deployment decision (Voyage AI is Anthropic's
    recommended embedder). This is the structural seam: it carries the model name +
    a lazily-resolved API key (secrets-in-env, never inline) and raises if asked to
    embed without a real client wired. The HTTP call lands when the provider is
    finalized in deployment; **tests never construct a live one** — they use
    :class:`FakeEmbeddingProvider`.
    """

    DEFAULT_MODEL = "voyage-3"
    DEFAULT_DIM = 1024

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        dim: int = DEFAULT_DIM,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._dim = dim

    @property
    def name(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> List[List[float]]:  # pragma: no cover
        raise NotImplementedError(
            "AnthropicEmbeddingProvider is a deployment-time seam stub; the live "
            "embedding HTTP call is wired at deploy. Tests use FakeEmbeddingProvider."
        )


# Config-driven registry: manifest `kb.embedding.provider` selects the adapter.
# The v1 DEFAULT provider is named here (KB_DESIGN §2 — "name the v1 default").
DEFAULT_EMBEDDING_PROVIDER = "voyage"

_EMBEDDING_PROVIDERS = {
    "voyage": AnthropicEmbeddingProvider,
    "anthropic": AnthropicEmbeddingProvider,
    "fake": FakeEmbeddingProvider,
}


def build_embedding_provider(
    provider: str = DEFAULT_EMBEDDING_PROVIDER,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> EmbeddingProvider:
    """Build the manifest-selected :class:`EmbeddingProvider` (default: voyage).

    Unknown providers raise ``ValueError`` so a config typo fails loudly rather
    than silently producing no embeddings. The returned object satisfies the
    runtime-checkable :class:`EmbeddingProvider` protocol.
    """
    key = provider.strip().lower()
    if key not in _EMBEDDING_PROVIDERS:
        raise ValueError(
            f"unknown embedding provider {provider!r}; expected one of "
            f"{sorted(_EMBEDDING_PROVIDERS)}"
        )
    if key == "fake":
        return FakeEmbeddingProvider()
    return AnthropicEmbeddingProvider(
        api_key=api_key, model=model or AnthropicEmbeddingProvider.DEFAULT_MODEL
    )


# ---------------------------------------------------------------------------
# Vector helpers (app-side cosine — D-2; no extension)
# ---------------------------------------------------------------------------
def _tokenize(txt: str) -> List[str]:
    """Whitespace + lowercasing tokenizer (deterministic, dependency-free)."""
    return re.findall(r"[a-z0-9]+", txt.lower())


def _l2_normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 if either is degenerate)."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def encode_embedding(vec: Sequence[float]) -> str:
    """Encode a float vector to the D-2 TEXT storage form (JSON array)."""
    return json.dumps([float(v) for v in vec])


def decode_embedding(blob: Optional[str]) -> Optional[List[float]]:
    """Decode the D-2 TEXT storage form back to a float vector (None if empty/bad)."""
    if not blob:
        return None
    try:
        value = json.loads(blob)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, list):
        return None
    try:
        return [float(v) for v in value]
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Chunking (KB_DESIGN §10 — ~512 tokens / 64 overlap, deterministic)
# ---------------------------------------------------------------------------
def chunk_text(
    body: str, *, chunk_tokens: int = CHUNK_TOKENS, overlap: int = CHUNK_OVERLAP
) -> List[str]:
    """Split ``body`` into overlapping ~``chunk_tokens``-token windows.

    Whitespace-tokenized, deterministic; consecutive windows overlap by
    ``overlap`` tokens so a fact spanning a window boundary survives in both. A
    body shorter than one window yields a single chunk. The original whitespace is
    not preserved (chunks are space-joined tokens) — adequate for embedding + FTS5
    indexing where token content, not layout, is what matters.
    """
    if chunk_tokens <= 0:
        raise ValueError("chunk_tokens must be positive")
    if not 0 <= overlap < chunk_tokens:
        raise ValueError("overlap must be in [0, chunk_tokens)")
    tokens = body.split()
    if not tokens:
        return []
    if len(tokens) <= chunk_tokens:
        return [" ".join(tokens)]
    step = chunk_tokens - overlap
    chunks: List[str] = []
    start = 0
    while start < len(tokens):
        window = tokens[start : start + chunk_tokens]
        chunks.append(" ".join(window))
        if start + chunk_tokens >= len(tokens):
            break
        start += step
    return chunks


def _content_hash(text_body: str) -> str:
    return hashlib.sha256(text_body.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The retrieval unit + store interface
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class KBChunk:
    """A retrieved KB chunk (the citation unit the C3.5a gate references)."""

    chunk_id: int
    client_id: int
    text: str
    authority: float
    source_type: str
    score: float = 0.0


class KBStore(Protocol):
    """The retrieval seam: chunk → embed → index → fused top-K."""

    def search(self, client_id: int, query: str, *, top_k: int = 5) -> List[KBChunk]:
        """Return the fused (cosine + FTS5/BM25) top-K chunks for a query."""
        ...


def _fts5_query(query: str) -> str:
    """Build a safe FTS5 MATCH expression from arbitrary user text.

    FTS5 MATCH has its own query syntax (operators, quoting). User text can contain
    characters that break it, so each token is double-quoted (FTS5 string literal)
    and OR-joined. Returns ``""`` when there are no usable tokens (caller skips the
    keyword leg). Double-quotes inside a token are escaped per FTS5 rules ("" ).
    """
    tokens = _tokenize(query)
    if not tokens:
        return ""
    quoted = ['"' + t.replace('"', '""') + '"' for t in tokens]
    return " OR ".join(quoted)


class SQLiteKBStore:
    """Hybrid KB store over the 058 ``autocm_kb_*`` tables (D-2 SQLite path).

    Owns the chunk→embed→index write pipeline (storing the embedding as TEXT and
    maintaining the ``autocm_kb_chunks_fts`` companion) and the hybrid read path
    (app-side cosine + FTS5/BM25 → RRF fusion). The caller owns the ``Connection``
    lifecycle (mirrors ``relay.db`` / ``db.cost``). ``org_id`` is supplied for cost
    attribution (``cost_events.org_id``); ``client_id`` is the per-tenant KB scope.
    """

    def __init__(self, conn: Connection, embedder: EmbeddingProvider) -> None:
        self._conn = conn
        self._embedder = embedder
        self._ensure_fts()

    # -- schema (idempotent FTS5 companion) ---------------------------------
    def _ensure_fts(self) -> None:
        """Create the FTS5 companion if absent (idempotent).

        The 058 .sql migration creates ``autocm_kb_chunks_fts`` as an
        external-content FTS5 table, but the SA-Core ``create_all`` test path (and
        any environment that built schema from ``schema.py``) cannot represent a
        virtual table — see schema.py D-2 note. Creating it here ``IF NOT EXISTS``
        means the store works in BOTH paths and never double-creates.
        """
        self._conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS autocm_kb_chunks_fts "
                "USING fts5(chunk_text, content='autocm_kb_chunks', content_rowid='id')"
            )
        )

    # -- write pipeline -----------------------------------------------------
    def index_source(
        self,
        *,
        org_id: str,
        client_id: int,
        source_id: int,
        body: str,
        source_type: str = "doc",
        authority: float = 0.5,
        metadata: Optional[Dict[str, object]] = None,
    ) -> List[int]:
        """Chunk → embed → index a source body; return the new chunk row ids.

        Pinned ~512/64 chunking (deterministic). Each chunk is embedded via the
        configured :class:`EmbeddingProvider`, stored with its TEXT-encoded vector
        (D-2), and indexed into the FTS5 companion. Embedding spend is logged to
        ``cost_events`` with the ``autocm.embed`` call_type.
        """
        chunks = chunk_text(body)
        if not chunks:
            return []
        vectors = self._embed(org_id, chunks)
        meta_json = json.dumps(metadata or {})
        ids: List[int] = []
        for chunk, vec in zip(chunks, vectors):
            row = self._conn.execute(
                text(
                    "INSERT INTO autocm_kb_chunks "
                    "(source_id, client_id, chunk_text, chunk_embedding, "
                    " chunk_metadata, chunk_authority, content_hash, status) "
                    "VALUES (:source_id, :client_id, :chunk_text, :embedding, "
                    " :meta, :authority, :chash, 'active') "
                    "RETURNING id"
                ),
                {
                    "source_id": source_id,
                    "client_id": client_id,
                    "chunk_text": chunk,
                    "embedding": encode_embedding(vec),
                    "meta": meta_json,
                    "authority": authority,
                    "chash": _content_hash(chunk),
                },
            ).fetchone()
            chunk_id = int(row[0])
            ids.append(chunk_id)
            # Maintain the external-content FTS5 index (rowid == chunk id).
            self._conn.execute(
                text(
                    "INSERT INTO autocm_kb_chunks_fts (rowid, chunk_text) "
                    "VALUES (:rowid, :chunk_text)"
                ),
                {"rowid": chunk_id, "chunk_text": chunk},
            )
        return ids

    def _embed(self, org_id: str, texts: Sequence[str]) -> List[List[float]]:
        """Embed a batch + log the spend to ``cost_events`` (``autocm.embed``)."""
        vectors = self._embedder.embed(texts)
        # Cost is logged even at $0 (the FakeEmbeddingProvider) so the call_type +
        # token accounting are present for the spend audit; a real provider sets a
        # nonzero cost_usd. token count == total tokens embedded (deterministic).
        total_tokens = sum(len(_tokenize(t)) for t in texts)
        try:
            log_cost(
                self._conn,
                org_id,
                EMBED_CALL_TYPE,
                0.0,
                model=self._embedder.name,
                input_tokens=total_tokens,
                output_tokens=0,
            )
        except Exception:  # pragma: no cover - cost logging must never block index
            logger.exception("autocm.embed cost logging failed (continuing)")
        return vectors

    # -- read pipeline (HYBRID: cosine + FTS5 → RRF fusion) -----------------
    def search(self, client_id: int, query: str, *, top_k: int = 5) -> List[KBChunk]:
        """Return the fused (cosine + FTS5/BM25) top-K active chunks for a client.

        Per ``KB_DESIGN §3`` step 2 the two legs are merged via reciprocal-rank
        fusion BEFORE C3.2c authority/recency weighting. Each leg is run wide
        (its own top-N), the rank lists are RRF-fused, and the fused top-K is
        returned. ``client_id`` filtering is unconditional (per-client isolation,
        KB_DESIGN §6). ``score`` on each returned chunk is its fused RRF score.
        """
        if top_k <= 0:
            return []
        # Run each leg wider than top_k so a chunk strong in one leg but weak in the
        # other still survives the fusion into the final top_k.
        leg_n = max(top_k * 4, top_k)
        cosine_ranked = self._cosine_leg(client_id, query, leg_n)
        keyword_ranked = self._keyword_leg(client_id, query, leg_n)
        fused = self._rrf_fuse(cosine_ranked, keyword_ranked, top_k)
        return fused

    def _cosine_leg(self, client_id: int, query: str, leg_n: int) -> List[int]:
        """App-side cosine top-N chunk ids (D-2; decode every stored vector)."""
        qvec = self._embedder.embed([query])[0]
        rows = self._conn.execute(
            text(
                "SELECT id, chunk_embedding FROM autocm_kb_chunks "
                "WHERE client_id = :client_id AND status = 'active'"
            ),
            {"client_id": client_id},
        ).fetchall()
        scored: List[Tuple[int, float]] = []
        for r in rows:
            vec = decode_embedding(r[1])
            if vec is None:
                continue
            scored.append((int(r[0]), cosine(qvec, vec)))
        # Highest cosine first; tie-break by chunk id for determinism.
        scored.sort(key=lambda iv: (-iv[1], iv[0]))
        return [cid for cid, score in scored[:leg_n] if score > 0.0]

    def _keyword_leg(self, client_id: int, query: str, leg_n: int) -> List[int]:
        """FTS5/BM25 top-N chunk ids (lexical leg; lower bm25 == better match)."""
        match = _fts5_query(query)
        if not match:
            return []
        # bm25() returns a score where MORE-NEGATIVE == better; ORDER BY bm25 ASC.
        # Join back to autocm_kb_chunks to enforce the per-client + active filter
        # (the FTS5 table itself has no client_id column).
        rows = self._conn.execute(
            text(
                "SELECT f.rowid FROM autocm_kb_chunks_fts f "
                "JOIN autocm_kb_chunks c ON c.id = f.rowid "
                "WHERE autocm_kb_chunks_fts MATCH :match "
                "  AND c.client_id = :client_id AND c.status = 'active' "
                "ORDER BY bm25(autocm_kb_chunks_fts) ASC, f.rowid ASC "
                "LIMIT :leg_n"
            ),
            {"match": match, "client_id": client_id, "leg_n": leg_n},
        ).fetchall()
        return [int(r[0]) for r in rows]

    def _rrf_fuse(
        self, cosine_ranked: List[int], keyword_ranked: List[int], top_k: int
    ) -> List[KBChunk]:
        """Reciprocal-rank fusion of the two ranked id lists → hydrated top-K.

        RRF score for a chunk = Σ 1/(k + rank) over the legs it appears in
        (rank is 1-based). A chunk surfaced by EITHER leg is a fusion candidate, so
        a keyword-only-matchable chunk that cosine misses still enters the top-K.
        """
        scores: Dict[int, float] = {}
        for rank, cid in enumerate(cosine_ranked, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank)
        for rank, cid in enumerate(keyword_ranked, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank)
        if not scores:
            return []
        # Highest fused score first; tie-break by chunk id for determinism.
        ordered = sorted(scores.items(), key=lambda iv: (-iv[1], iv[0]))[:top_k]
        return self._hydrate([cid for cid, _ in ordered], scores)

    def _hydrate(
        self, chunk_ids: List[int], scores: Dict[int, float]
    ) -> List[KBChunk]:
        """Load the chunk rows for ``chunk_ids`` (preserving order) into KBChunks."""
        if not chunk_ids:
            return []
        rows = self._conn.execute(
            text(
                "SELECT c.id, c.client_id, c.chunk_text, c.chunk_authority, "
                "       s.source_type "
                "FROM autocm_kb_chunks c "
                "JOIN autocm_kb_sources s ON s.id = c.source_id "
                "WHERE c.id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": chunk_ids},
        ).fetchall()
        by_id = {int(r[0]): r for r in rows}
        out: List[KBChunk] = []
        for cid in chunk_ids:
            r = by_id.get(cid)
            if r is None:
                continue
            out.append(
                KBChunk(
                    chunk_id=int(r[0]),
                    client_id=int(r[1]),
                    text=r[2],
                    authority=float(r[3]),
                    source_type=r[4],
                    score=scores.get(cid, 0.0),
                )
            )
        return out


class NotImplementedKBStore:
    """Stub store retained for callers that haven't wired a real store yet.

    Raises so accidental hot-path use is loud. The real path is
    :class:`SQLiteKBStore`.
    """

    def search(self, client_id: int, query: str, *, top_k: int = 5) -> List[KBChunk]:
        raise NotImplementedError("use SQLiteKBStore (C3.2a)")


__all__ = [
    # retrieval unit + interface
    "KBChunk",
    "KBStore",
    "SQLiteKBStore",
    "NotImplementedKBStore",
    # embedding seam
    "EmbeddingProvider",
    "FakeEmbeddingProvider",
    "AnthropicEmbeddingProvider",
    "build_embedding_provider",
    "DEFAULT_EMBEDDING_PROVIDER",
    "EMBED_CALL_TYPE",
    # chunking + vector helpers
    "chunk_text",
    "cosine",
    "encode_embedding",
    "decode_embedding",
    "CHUNK_TOKENS",
    "CHUNK_OVERLAP",
]
