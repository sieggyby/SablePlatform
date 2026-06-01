"""KB refresher + authority/recency retrieval + freshness gate (C3.2c).

Three load-bearing surfaces, all owned here per ``MEGAPLAN C3.2c`` and
``KB_DESIGN.md``:

1. **Scheduled refresh per freshness contract** (``KB_DESIGN §5``). Each
   ``autocm_kb_sources`` row has a max-staleness contract derived from its
   ``refresh_cadence`` / ``source_type``. :class:`KBRefresher.refresh_client`
   finds every *due* source (``last_refreshed_at`` older than its contract, vs.
   an injectable :class:`Clock` so tests use a deterministic fake), re-extracts
   it through the C3.2b :class:`~sable_platform.autocm.kb.extractor.KBExtractor`,
   re-indexes via the C3.2a store, and stamps ``last_refreshed_at`` /
   ``last_changed_at``. Immutable sources (audit / whitepaper) are *never* due.
   The scheduled trigger is the SP ``WorkflowRunner`` workflow
   ``autocm_kb_refresh`` (registered in
   ``sable_platform/workflows/builtins/autocm_kb_refresh.py``).

2. **Authority-tiered + recency-weighted retrieval ranking** (``KB_DESIGN §3``
   steps 3–4). :func:`rank_chunks` runs *over the C3.2a FUSED cosine+FTS5/BM25
   result set* (NOT a vector-only set — the weighting is applied AFTER fusion per
   ``MEGAPLAN C3.2c``). It boosts higher-authority chunks (audit/contract 1.0 >
   whitepaper/pinned 0.9 > docs/substack 0.8 > recent tweets 0.7 > thesis 0.5)
   and, for time-sensitive queries ("last X" / "current Y" / status), boosts
   chunks whose source refreshed recently.

3. **Freshness-contract gate** (``KB_DESIGN §5``). :func:`check_cited_freshness`
   inspects the chunks a draft cited; if ANY cited chunk's source is older than
   its freshness contract, it returns a :class:`FreshnessVerdict` whose
   ``downgrade_to_hitl`` signal is True. The C3.5a gate consumes this signal to
   auto-downgrade an otherwise-autonomous draft to HITL.

PLUS the **resolved-FAQ → KB promotion write handler**
(:func:`promote_resolved_faq`): reads an ``autocm_digest_interactions`` row
(``action='approve_for_kb'``) and inserts a canonical high-authority (0.8)
``autocm_kb_chunks`` row with provenance ``source_type='resolved_faq'``. This is
the C3.2c-owned KB WRITE path (hard-pinned here, not C3.7). It is unit-testable
with a synthetic interaction row (no dependency on the C3.7 digest UI existing).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from sable_platform.db.audit import log_audit

from .extractor import KBExtractor
from .store import KBChunk, SQLiteKBStore, _content_hash, _tokenize

logger = logging.getLogger(__name__)

# Provenance / authority for an operator-/founder-approved resolved-FAQ chunk
# (KB_DESIGN §1 "Resolved FAQ from digest ... 0.8 ... High-authority canonical").
RESOLVED_FAQ_SOURCE_TYPE = "resolved_faq"
RESOLVED_FAQ_AUTHORITY = 0.8

# log_audit action verbs (audit-everything convention; source="sable-autocm").
AUDIT_SOURCE = "sable-autocm"
ACTION_KB_REFRESH = "kb_source_refreshed"
ACTION_KB_PROMOTE = "kb_resolved_faq_promoted"
ACTION_KB_STALE_DOWNGRADE = "kb_stale_cited_downgrade"


# ---------------------------------------------------------------------------
# Clock seam (injectable; a fake clock drives the refresh scheduler in tests)
# ---------------------------------------------------------------------------
Clock = Callable[[], datetime]
"""A no-arg callable returning the current wall-clock time (tz-aware UTC).

Production passes :func:`utc_now`; tests pass a closure over a mutable cell so
the freshness scheduler can be advanced deterministically (mirrors the
``onchain`` adapter's ``clock`` seam, but wall-clock instead of monotonic).
"""


def utc_now() -> datetime:
    """The default production :data:`Clock` — tz-aware UTC now."""
    return datetime.now(timezone.utc)


def _iso_z(dt: datetime) -> str:
    """Render a datetime to the 058 TEXT timestamp form (``...Z``, no micros)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse a stored ``autocm_*`` TEXT timestamp → tz-aware UTC datetime.

    Tolerates the canonical ``...Z`` form, an explicit ``+00:00`` offset, and a
    naive form (assumed UTC). Returns ``None`` on empty/unparseable input so a
    NULL ``last_refreshed_at`` (never refreshed) is handled by the caller.
    """
    if not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Freshness contracts (KB_DESIGN §5 + §1 cadence column)
# ---------------------------------------------------------------------------
# Per-source max-staleness. ``None`` == immutable (audit / whitepaper) — a source
# whose contract is None is NEVER due for a scheduled re-fetch. Keyed by a
# normalized cadence token AND by source_type so either field can drive it.
_FOREVER = None  # sentinel for "immutable per version" (never stale)

# refresh_cadence (KB_DESIGN §1 "Refresh cadence" column) → max staleness.
_CADENCE_CONTRACTS: Dict[str, Optional[timedelta]] = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(hours=24),
    "weekly": timedelta(days=7),
    "live": timedelta(seconds=60),  # on-chain 60s cache (not doc-refreshed here)
    "immutable": _FOREVER,
    "on_change": _FOREVER,  # event-driven, not time-scheduled
    "on_pinned_change": _FOREVER,
    "on_add": _FOREVER,
    "on_approval": _FOREVER,
    "on_query": timedelta(seconds=60),
}

# source_type → max staleness (KB_DESIGN §5 explicit per-type contracts), used
# when refresh_cadence is absent/unrecognized. Mirrors the §1 table authority rows.
_SOURCE_TYPE_CONTRACTS: Dict[str, Optional[timedelta]] = {
    "audit": _FOREVER,
    "whitepaper": _FOREVER,
    "tokenomics": _FOREVER,
    "pinned_tweet": timedelta(hours=24),
    "pinned": timedelta(hours=24),
    "recent_tweet": timedelta(hours=1),
    "recent_tweets": timedelta(hours=1),
    "substack": timedelta(hours=24),
    "substack_thesis": timedelta(days=7),
    "docs": timedelta(days=7),
    "doc": timedelta(days=7),
    "website": timedelta(days=7),
    "web": timedelta(days=7),
    "contract": timedelta(days=7),
    "github": timedelta(days=7),
    "onchain": timedelta(seconds=60),
    "resolved_faq": _FOREVER,
    "transcript": _FOREVER,
    "manual": _FOREVER,
}

# Default contract when neither cadence nor source_type is recognized: treat as
# weekly-refreshable (the KB_DESIGN §5 "Docs: 7d" floor) rather than immutable, so
# an unknown source still gets swept rather than silently going stale forever.
_DEFAULT_CONTRACT = timedelta(days=7)


def freshness_contract(
    *, refresh_cadence: Optional[str], source_type: Optional[str]
) -> Optional[timedelta]:
    """Resolve a source's max-staleness contract (``None`` == immutable).

    Precedence (KB_DESIGN §1 names the cadence per-row, §5 names it per-type):
    an explicit ``refresh_cadence`` wins; otherwise fall back to the
    ``source_type`` contract; otherwise the weekly default. An immutable
    cadence/type (audit, whitepaper, on-change) resolves to ``None`` and is never
    scheduled for re-fetch.
    """
    if refresh_cadence:
        token = refresh_cadence.strip().lower().replace("-", "_").replace(" ", "_")
        if token in _CADENCE_CONTRACTS:
            return _CADENCE_CONTRACTS[token]
    if source_type:
        st = source_type.strip().lower()
        if st in _SOURCE_TYPE_CONTRACTS:
            return _SOURCE_TYPE_CONTRACTS[st]
    return _DEFAULT_CONTRACT


def is_source_due(
    *,
    refresh_cadence: Optional[str],
    source_type: Optional[str],
    last_refreshed_at: Optional[str],
    now: datetime,
) -> bool:
    """True if a source is past its freshness contract and due for a re-fetch.

    Immutable contracts (``None``) are never due. A source that has never been
    refreshed (NULL ``last_refreshed_at``) IS due (it has no fresh content yet),
    unless its contract is immutable.
    """
    contract = freshness_contract(
        refresh_cadence=refresh_cadence, source_type=source_type
    )
    if contract is None:
        return False
    last = parse_iso(last_refreshed_at)
    if last is None:
        return True
    return (now - last) >= contract


# ---------------------------------------------------------------------------
# Authority-tiered + recency-weighted retrieval ranking (KB_DESIGN §3 steps 3-4)
# ---------------------------------------------------------------------------
# Time-sensitivity cue tokens (KB_DESIGN §3 step 4: "if the question is
# time-sensitive ('last X' / 'current Y' / status questions)").
#
# Single-token cues are matched on WORD BOUNDARIES (via the tokenizer), NOT raw
# substrings — otherwise "now" matches inside "knows"/"renowned" and "live"
# matches inside "liveness"/"delivery", wrongly biasing a stable-fact query
# toward the newest chunk (the exact §3-step-4 hazard the docstring warns about).
_RECENCY_CUE_TOKENS = frozenset(
    {
        "last",
        "latest",
        "current",
        "currently",
        "now",
        "today",
        "recent",
        "recently",
        "status",
        "live",
    }
)
# Multi-word cues are phrases — matched by substring on the lowered query, since a
# token-set test cannot capture word ORDER ("as of", not "of as").
_RECENCY_CUE_PHRASES = (
    "as of",
    "right now",
    "up to date",
)

# Half-life for the recency boost: a chunk indexed this long ago has its recency
# component halved. 7d matches the KB_DESIGN §5 "Docs: 7d" doc-refresh contract —
# fresher-than-a-week content is meaningfully boosted on time-sensitive queries.
_RECENCY_HALFLIFE = timedelta(days=7)


def is_time_sensitive(query: str) -> bool:
    """Heuristic: does the query ask for a 'last/current/status' fact (§3 step 4)?

    Single-word cues are tested against the query's TOKEN SET (word boundaries) so
    "now"/"live" never spuriously match inside "knows"/"renowned"/"liveness";
    multi-word cues are matched as phrases via substring on the lowered query.
    """
    q = query.lower()
    if any(phrase in q for phrase in _RECENCY_CUE_PHRASES):
        return True
    return bool(_RECENCY_CUE_TOKENS.intersection(_tokenize(q)))


@dataclass(frozen=True)
class RankedChunk:
    """A KB chunk after authority/recency re-weighting (the §3 step-3/4 output).

    Wraps the C3.2a :class:`KBChunk` (carried verbatim so the C3.5a gate still
    sees the original citation metadata) and adds the components that produced the
    final ranking, so the weighting is auditable / explainable.
    """

    chunk: KBChunk
    fused_score: float  # the C3.2a RRF score this ranking was applied OVER
    authority: float
    recency_boost: float
    rank_score: float  # the final score rank order is by (higher == better)


def _recency_boost(indexed_at: Optional[str], now: datetime) -> float:
    """A 0..1 recency factor with a 7d half-life (1.0 == just indexed)."""
    ts = parse_iso(indexed_at)
    if ts is None:
        return 0.0
    age = now - ts
    if age.total_seconds() <= 0:
        return 1.0
    half_lives = age.total_seconds() / _RECENCY_HALFLIFE.total_seconds()
    return 0.5 ** half_lives


def rank_chunks(
    fused: Sequence[KBChunk],
    query: str,
    *,
    now: datetime,
    indexed_at_by_id: Optional[Dict[int, str]] = None,
    authority_weight: float = 0.5,
    recency_weight: float = 0.3,
) -> List[RankedChunk]:
    """Authority-tier + recency re-weight the C3.2a FUSED result set (§3 steps 3-4).

    Runs OVER the already-fused cosine+FTS5/BM25 list (``MEGAPLAN C3.2c``: the
    weighting is applied AFTER hybrid fusion, never over a vector-only set). The
    final score is::

        rank_score = fused_score
                   + authority_weight * authority
                   + recency_weight   * recency_boost      (time-sensitive only)

    Authority always boosts (§3 step 3 "boost higher-authority chunks"). The
    recency term is only added for time-sensitive queries (§3 step 4) so a stable
    "what is the contract address" query is not biased toward the newest doc.
    Ties break by authority, then recency, then chunk id (deterministic).
    """
    indexed_at_by_id = indexed_at_by_id or {}
    time_sensitive = is_time_sensitive(query)
    ranked: List[RankedChunk] = []
    for c in fused:
        recency = (
            _recency_boost(indexed_at_by_id.get(c.chunk_id), now)
            if time_sensitive
            else 0.0
        )
        score = c.score + authority_weight * c.authority
        if time_sensitive:
            score += recency_weight * recency
        ranked.append(
            RankedChunk(
                chunk=c,
                fused_score=c.score,
                authority=c.authority,
                recency_boost=recency,
                rank_score=score,
            )
        )
    ranked.sort(
        key=lambda rc: (
            -rc.rank_score,
            -rc.authority,
            -rc.recency_boost,
            rc.chunk.chunk_id,
        )
    )
    return ranked


# ---------------------------------------------------------------------------
# Freshness-contract gate (KB_DESIGN §5: stale cited chunk → HITL downgrade)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StaleCitation:
    """One cited chunk whose source breached its freshness contract."""

    chunk_id: int
    source_id: int
    source_type: str
    refresh_cadence: Optional[str]
    last_refreshed_at: Optional[str]
    age_seconds: float
    contract_seconds: float


@dataclass(frozen=True)
class FreshnessVerdict:
    """The §5 gate result — the signal the C3.5a gate consumes.

    ``downgrade_to_hitl`` is True iff at least one cited chunk's source is older
    than its freshness contract. ``stale`` enumerates the offenders so the HITL
    queue / audit row can say WHICH cited fact went stale.
    """

    downgrade_to_hitl: bool
    stale: List[StaleCitation] = field(default_factory=list)


def check_cited_freshness(
    conn: Connection,
    cited_chunk_ids: Sequence[int],
    *,
    now: datetime,
    client_id: Optional[int] = None,
) -> FreshnessVerdict:
    """Gate a draft's cited chunks against their sources' freshness contracts.

    ``KB_DESIGN §5``: "Drafter checks the staleness of chunks it uses; if any
    cited chunk is older than its contract, draft is downgraded to HITL
    automatically." Returns a :class:`FreshnessVerdict` exposing the boolean
    downgrade signal + the offending citations. An empty citation list is fresh
    (nothing to gate). Staleness is measured from the SOURCE's
    ``last_refreshed_at`` (the source freshness, per the contract), falling back
    to the chunk's ``indexed_at`` when the source was never explicitly refreshed.

    ``client_id`` (KB_DESIGN §6 per-client isolation): when supplied, the gate
    ANDs it into the lookup so any out-of-scope chunk id is silently IGNORED
    (never gated against a different client's source). Callers MUST pass ids that
    originate from the same client's retrieval result (``search``/
    ``search_and_rank`` are already client-scoped); ``client_id`` makes that
    invariant structural rather than relying on the caller alone.
    """
    ids = [int(c) for c in cited_chunk_ids]
    if not ids:
        return FreshnessVerdict(downgrade_to_hitl=False)
    sql = (
        "SELECT c.id, s.id, s.source_type, s.refresh_cadence, "
        "       s.last_refreshed_at, c.indexed_at "
        "FROM autocm_kb_chunks c "
        "JOIN autocm_kb_sources s ON s.id = c.source_id "
        "WHERE c.id IN :ids"
    )
    params: Dict[str, object] = {"ids": ids}
    if client_id is not None:
        sql += " AND c.client_id = :client_id"
        params["client_id"] = int(client_id)
    rows = conn.execute(
        text(sql).bindparams(bindparam("ids", expanding=True)),
        params,
    ).fetchall()
    stale: List[StaleCitation] = []
    for r in rows:
        chunk_id = int(r[0])
        source_id = int(r[1])
        source_type = r[2] or ""
        refresh_cadence = r[3]
        source_refreshed = r[4]
        chunk_indexed = r[5]
        contract = freshness_contract(
            refresh_cadence=refresh_cadence, source_type=source_type
        )
        if contract is None:  # immutable source — never stale
            continue
        # Source freshness drives the contract; fall back to the chunk's own
        # indexed_at if the source has no recorded refresh.
        anchor = parse_iso(source_refreshed) or parse_iso(chunk_indexed)
        if anchor is None:
            # No timestamp at all on a time-bounded source → treat as stale.
            stale.append(
                StaleCitation(
                    chunk_id=chunk_id,
                    source_id=source_id,
                    source_type=source_type,
                    refresh_cadence=refresh_cadence,
                    last_refreshed_at=source_refreshed,
                    age_seconds=float("inf"),
                    contract_seconds=contract.total_seconds(),
                )
            )
            continue
        age = now - anchor
        if age >= contract:
            stale.append(
                StaleCitation(
                    chunk_id=chunk_id,
                    source_id=source_id,
                    source_type=source_type,
                    refresh_cadence=refresh_cadence,
                    last_refreshed_at=source_refreshed,
                    age_seconds=age.total_seconds(),
                    contract_seconds=contract.total_seconds(),
                )
            )
    return FreshnessVerdict(downgrade_to_hitl=bool(stale), stale=stale)


# ---------------------------------------------------------------------------
# Resolved-FAQ → KB promotion write handler (DIGEST §2e/§4, KB_DESIGN §1/§8)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PromotionResult:
    """The outcome of promoting one ``approve_for_kb`` interaction to a KB chunk."""

    chunk_id: int
    source_id: int
    client_id: int
    chunk_text: str


def promote_resolved_faq(
    conn: Connection,
    interaction_id: int,
    *,
    actor: str,
    org_id: Optional[str] = None,
) -> PromotionResult:
    """Promote an ``approve_for_kb`` digest interaction into a canonical KB chunk.

    Reads the ``autocm_digest_interactions`` row, validates it is an
    ``approve_for_kb`` action, materializes a high-authority (0.8)
    ``resolved_faq`` source row (idempotent per client — one
    ``resolved_faq`` source per client) and inserts the canonical answer text as
    an ``autocm_kb_chunks`` row (authority 0.8, ``status='active'``), keeping the
    FTS5 companion in sync. Writes a ``kb_resolved_faq_promoted`` audit row.

    The answer text is read from the interaction ``payload`` JSON (key
    ``chunk_text``/``answer``/``text``); ``target_ref`` may name the originating
    question. This is the C3.2c-owned KB WRITE path (hard-pinned here per
    ``MEGAPLAN C3.2c``); it is unit-testable with a synthetic interaction row —
    NO dependency on the C3.7 digest UI existing.
    """
    row = conn.execute(
        text(
            "SELECT id, client_id, action, target_ref, payload, digest_period "
            "FROM autocm_digest_interactions WHERE id = :id"
        ),
        {"id": interaction_id},
    ).fetchone()
    if row is None:
        raise ValueError(f"autocm_digest_interactions id {interaction_id} not found")
    client_id = int(row[1])
    action = row[2]
    if action != "approve_for_kb":
        raise ValueError(
            f"interaction {interaction_id} action is {action!r}; "
            "only 'approve_for_kb' promotes to KB"
        )
    target_ref = row[3]
    try:
        payload = json.loads(row[4] or "{}")
    except (TypeError, ValueError):
        payload = {}
    digest_period = row[5]

    chunk_body = (
        payload.get("chunk_text")
        or payload.get("answer")
        or payload.get("text")
        or ""
    ).strip()
    if not chunk_body:
        raise ValueError(
            f"interaction {interaction_id} payload has no chunk_text/answer/text "
            "to promote"
        )

    source_id = _ensure_resolved_faq_source(conn, client_id)

    metadata = {
        "promoted_from_interaction": interaction_id,
        "question": target_ref,
        "digest_period": digest_period,
    }
    chunk_row = conn.execute(
        text(
            "INSERT INTO autocm_kb_chunks "
            "(source_id, client_id, chunk_text, chunk_embedding, chunk_metadata, "
            " chunk_authority, content_hash, status) "
            "VALUES (:source_id, :client_id, :chunk_text, NULL, :meta, "
            " :authority, :chash, 'active') "
            "RETURNING id"
        ),
        {
            "source_id": source_id,
            "client_id": client_id,
            "chunk_text": chunk_body,
            "meta": json.dumps(metadata),
            "authority": RESOLVED_FAQ_AUTHORITY,
            "chash": _content_hash(chunk_body),
        },
    ).fetchone()
    chunk_id = int(chunk_row[0])
    # Keep the FTS5 keyword leg in sync (the store creates this companion table).
    conn.execute(
        text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS autocm_kb_chunks_fts "
            "USING fts5(chunk_text, content='autocm_kb_chunks', content_rowid='id')"
        )
    )
    conn.execute(
        text(
            "INSERT INTO autocm_kb_chunks_fts (rowid, chunk_text) "
            "VALUES (:rowid, :chunk_text)"
        ),
        {"rowid": chunk_id, "chunk_text": chunk_body},
    )

    log_audit(
        conn,
        actor=actor,
        action=ACTION_KB_PROMOTE,
        org_id=org_id,
        entity_id=str(chunk_id),
        detail={
            "interaction_id": interaction_id,
            "client_id": client_id,
            "source_id": source_id,
            "source_type": RESOLVED_FAQ_SOURCE_TYPE,
            "authority": RESOLVED_FAQ_AUTHORITY,
            "question": target_ref,
        },
        source=AUDIT_SOURCE,
    )
    return PromotionResult(
        chunk_id=chunk_id,
        source_id=source_id,
        client_id=client_id,
        chunk_text=chunk_body,
    )


def _ensure_resolved_faq_source(conn: Connection, client_id: int) -> int:
    """Return the client's singleton ``resolved_faq`` source id (create if absent).

    All promoted FAQs share one ``resolved_faq`` source per client (KB_DESIGN §1
    "Resolved FAQ from digest"). The source is immutable-cadence (``on_approval``)
    so the refresh sweep never re-fetches it — promoted FAQs are canonical, not
    re-scraped.
    """
    existing = conn.execute(
        text(
            "SELECT id FROM autocm_kb_sources "
            "WHERE client_id = :c AND source_type = :st LIMIT 1"
        ),
        {"c": client_id, "st": RESOLVED_FAQ_SOURCE_TYPE},
    ).fetchone()
    if existing is not None:
        return int(existing[0])
    row = conn.execute(
        text(
            "INSERT INTO autocm_kb_sources "
            "(client_id, source_type, refresh_cadence, authority_default, status) "
            "VALUES (:c, :st, 'on_approval', :a, 'active') RETURNING id"
        ),
        {"c": client_id, "st": RESOLVED_FAQ_SOURCE_TYPE, "a": RESOLVED_FAQ_AUTHORITY},
    ).fetchone()
    return int(row[0])


# ---------------------------------------------------------------------------
# The scheduled refresher (driven by the SP WorkflowRunner workflow)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RefreshOutcome:
    """Per-source result of one refresh sweep."""

    source_id: int
    refreshed: bool
    changed: bool
    new_chunk_ids: List[int] = field(default_factory=list)
    error: Optional[str] = None


class KBRefresher:
    """Re-extract + re-embed stale sources per their freshness contract (C3.2c).

    Holds a C3.2b :class:`KBExtractor` (the fetch+normalize legs) and a C3.2a
    :class:`SQLiteKBStore` (embed+index). The :data:`Clock` seam is injected so
    the freshness scheduler is deterministic under test (a fake clock advances
    time; no wall-clock dependency). ``org_id`` is needed for cost attribution on
    the re-embed (``cost_events`` via the store).

    Change detection: a source whose newly-extracted content hashes identically
    to its current active chunks is NOT re-indexed (``changed=False``) — only the
    ``last_refreshed_at`` stamp is bumped. A source whose content changed marks
    its prior chunks ``stale`` and indexes the new chunks (``KB_DESIGN §9`` —
    source re-fetch supersedes stale content).
    """

    def __init__(
        self,
        conn: Connection,
        store: SQLiteKBStore,
        extractor: KBExtractor,
        *,
        org_id: str,
        clock: Clock = utc_now,
    ) -> None:
        self._conn = conn
        self._store = store
        self._extractor = extractor
        self._org_id = org_id
        self._clock = clock

    def due_sources(self, client_id: int) -> List[int]:
        """Return the ids of a client's sources currently past their contract."""
        now = self._clock()
        rows = self._conn.execute(
            text(
                "SELECT id, source_type, refresh_cadence, last_refreshed_at "
                "FROM autocm_kb_sources "
                "WHERE client_id = :c AND status = 'active' "
                "ORDER BY id"
            ),
            {"c": client_id},
        ).fetchall()
        due: List[int] = []
        for r in rows:
            if is_source_due(
                refresh_cadence=r[2],
                source_type=r[1],
                last_refreshed_at=r[3],
                now=now,
            ):
                due.append(int(r[0]))
        return due

    def refresh_client(self, client_id: int) -> int:
        """Refresh every due source for a client; return the count refreshed.

        Satisfies the :class:`KBRefresher` protocol contract from C3.1. Each due
        source is refreshed via :meth:`refresh_source`; a source that errors is
        recorded (``last_error``) but does NOT abort the sweep (a dead source must
        not block the rest).
        """
        refreshed = 0
        for source_id in self.due_sources(client_id):
            outcome = self.refresh_source(source_id)
            if outcome.refreshed:
                refreshed += 1
        return refreshed

    def refresh_source(self, source_id: int) -> RefreshOutcome:
        """Re-extract + (if changed) re-index a single source; stamp timestamps."""
        now = self._clock()
        now_iso = _iso_z(now)
        try:
            extracted = self._extractor.extract_source(self._conn, source_id)
        except Exception as exc:  # noqa: BLE001 - a dead source must not crash sweep
            logger.warning("KB refresh: source %s extract failed: %s", source_id, exc)
            self._conn.execute(
                text(
                    "UPDATE autocm_kb_sources "
                    "SET last_error = :err, last_refreshed_at = :now WHERE id = :id"
                ),
                {"err": str(exc)[:500], "now": now_iso, "id": source_id},
            )
            return RefreshOutcome(
                source_id=source_id, refreshed=True, changed=False, error=str(exc)
            )

        new_hashes = {_content_hash(block) for block in extracted.chunks}
        current_hashes = self._active_content_hashes(source_id)
        changed = bool(extracted.chunks) and new_hashes != current_hashes

        new_ids: List[int] = []
        if changed:
            # KB_DESIGN §9: a re-fetch supersedes the prior content. Mark the old
            # active chunks stale (still searchable-but-deprioritized history is a
            # C3.2a/C3.5c concern; here we deactivate so retrieval prefers fresh).
            self._mark_source_chunks_stale(source_id)
            new_ids = self._extractor.index_source(
                self._conn, self._store, org_id=self._org_id, source_id=source_id
            )

        self._conn.execute(
            text(
                "UPDATE autocm_kb_sources "
                "SET last_refreshed_at = :now, "
                "    last_changed_at = CASE WHEN :changed = 1 THEN :now "
                "                          ELSE last_changed_at END, "
                "    last_error = NULL "
                "WHERE id = :id"
            ),
            {"now": now_iso, "changed": 1 if changed else 0, "id": source_id},
        )
        log_audit(
            self._conn,
            actor="sable-autocm",
            action=ACTION_KB_REFRESH,
            org_id=self._org_id,
            entity_id=str(source_id),
            detail={
                "source_id": source_id,
                "changed": changed,
                "new_chunk_count": len(new_ids),
            },
            source=AUDIT_SOURCE,
        )
        return RefreshOutcome(
            source_id=source_id,
            refreshed=True,
            changed=changed,
            new_chunk_ids=new_ids,
        )

    def _active_content_hashes(self, source_id: int) -> set:
        rows = self._conn.execute(
            text(
                "SELECT content_hash FROM autocm_kb_chunks "
                "WHERE source_id = :s AND status = 'active'"
            ),
            {"s": source_id},
        ).fetchall()
        return {r[0] for r in rows if r[0] is not None}

    def _mark_source_chunks_stale(self, source_id: int) -> None:
        self._conn.execute(
            text(
                "UPDATE autocm_kb_chunks SET status = 'stale' "
                "WHERE source_id = :s AND status = 'active'"
            ),
            {"s": source_id},
        )


# ---------------------------------------------------------------------------
# Convenience: rank a stored search result (store fused-search → ranked list)
# ---------------------------------------------------------------------------
def search_and_rank(
    conn: Connection,
    store: SQLiteKBStore,
    client_id: int,
    query: str,
    *,
    now: datetime,
    top_k: int = 5,
) -> List[RankedChunk]:
    """C3.2a fused search → C3.2c authority/recency re-rank, in one call.

    Runs the store's hybrid (cosine+FTS5/BM25) fused search WIDE, then applies the
    §3 step-3/4 authority/recency weighting OVER that fused set and returns the
    top-K ranked chunks. This is the canonical retrieval entry point a drafter
    uses; ``indexed_at`` per chunk is loaded so the recency leg has real data.
    """
    fused = store.search(client_id, query, top_k=max(top_k * 4, top_k))
    if not fused:
        return []
    indexed_at_by_id = _load_indexed_at(conn, [c.chunk_id for c in fused])
    ranked = rank_chunks(
        fused, query, now=now, indexed_at_by_id=indexed_at_by_id
    )
    return ranked[:top_k]


def _load_indexed_at(conn: Connection, chunk_ids: Sequence[int]) -> Dict[int, str]:
    ids = [int(c) for c in chunk_ids]
    if not ids:
        return {}
    rows = conn.execute(
        text(
            "SELECT id, indexed_at FROM autocm_kb_chunks WHERE id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    ).fetchall()
    return {int(r[0]): r[1] for r in rows}


# ---------------------------------------------------------------------------
# Protocol kept for callers wired against C3.1's stub interface
# ---------------------------------------------------------------------------
class NotImplementedRefresher:
    """Stub refresher retained for callers not yet wired to the real one.

    Raises so accidental hot-path use is loud; the real path is
    :class:`KBRefresher`.
    """

    def refresh_client(self, client_id: int) -> int:
        raise NotImplementedError("use KBRefresher (C3.2c)")


__all__ = [
    # scheduled refresh
    "KBRefresher",
    "NotImplementedRefresher",
    "RefreshOutcome",
    # clock seam
    "Clock",
    "utc_now",
    "parse_iso",
    # freshness contracts
    "freshness_contract",
    "is_source_due",
    "RESOLVED_FAQ_SOURCE_TYPE",
    "RESOLVED_FAQ_AUTHORITY",
    # authority/recency ranking
    "rank_chunks",
    "RankedChunk",
    "is_time_sensitive",
    "search_and_rank",
    # freshness gate (stale-cited → HITL)
    "check_cited_freshness",
    "FreshnessVerdict",
    "StaleCitation",
    # resolved-FAQ → KB promotion write handler
    "promote_resolved_faq",
    "PromotionResult",
]
