"""C3.2c — kb.refresher tests (freshness contracts + authority/recency + gate).

Exit-criterion coverage (MEGAPLAN C3.2c exit/audit):
  * a seeded RM KB returns AUTHORITY-RANKED chunks (authority weighting over the
    C3.2a FUSED result set, applied AFTER hybrid fusion — KB_DESIGN §3 step 3);
    recency weighting boosts fresh chunks on time-sensitive queries (§3 step 4);
  * a STALE cited chunk auto-DOWNGRADES the draft to HITL (the freshness-contract
    gate exposes the downgrade signal — KB_DESIGN §5);
  * the scheduled refresher (driven by an SP WorkflowRunner workflow + a FAKE
    clock here) re-fetches only sources past their freshness contract and leaves
    immutable sources alone (KB_DESIGN §5 / §1);
  * the resolved-FAQ → KB promotion WRITE HANDLER, given a SYNTHETIC
    ``autocm_digest_interactions`` row, writes a canonical high-authority (0.8)
    ``resolved_faq`` chunk (DIGEST §2e/§4, KB_DESIGN §1/§8) — no C3.7 dependency.

All offline — FakeEmbeddingProvider + FakeHttpFetcher + a fake clock cell. NO real
network, NO real embedding API, NO wall-clock dependency.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from sable_platform.autocm.kb.extractor import FakeHttpFetcher, KBExtractor
from sable_platform.autocm.kb.refresher import (
    KBRefresher,
    RESOLVED_FAQ_AUTHORITY,
    RESOLVED_FAQ_SOURCE_TYPE,
    check_cited_freshness,
    freshness_contract,
    is_source_due,
    is_time_sensitive,
    promote_resolved_faq,
    rank_chunks,
    search_and_rank,
)
from sable_platform.autocm.kb.store import FakeEmbeddingProvider, KBChunk, SQLiteKBStore

# A fixed "now" so every relative timestamp in the suite is deterministic.
NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


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


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_source(
    conn,
    client_id: int,
    *,
    source_type: str,
    authority: float = 0.8,
    refresh_cadence=None,
    last_refreshed_at=None,
    fetch_config=None,
    source_url=None,
) -> int:
    row = conn.execute(
        text(
            "INSERT INTO autocm_kb_sources "
            "(client_id, source_type, source_url, refresh_cadence, "
            " authority_default, fetch_config, last_refreshed_at) "
            "VALUES (:c, :st, :url, :cad, :a, :fc, :lr) RETURNING id"
        ),
        {
            "c": client_id,
            "st": source_type,
            "url": source_url,
            "cad": refresh_cadence,
            "a": authority,
            "fc": json.dumps(fetch_config or {}),
            "lr": last_refreshed_at,
        },
    ).fetchone()
    return int(row[0])


def _insert_chunk(
    conn,
    *,
    source_id: int,
    client_id: int,
    body: str,
    authority: float,
    indexed_at: str,
) -> int:
    row = conn.execute(
        text(
            "INSERT INTO autocm_kb_chunks "
            "(source_id, client_id, chunk_text, chunk_authority, status, indexed_at) "
            "VALUES (:s, :c, :t, :a, 'active', :ix) RETURNING id"
        ),
        {"s": source_id, "c": client_id, "t": body, "a": authority, "ix": indexed_at},
    ).fetchone()
    return int(row[0])


@pytest.fixture
def kb(sa_org):
    """(conn, org_id, client_id) with a seeded AutoCM client."""
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    conn.commit()
    return conn, org_id, client_id


# ---------------------------------------------------------------------------
# freshness contracts (KB_DESIGN §5 + §1 cadence)
# ---------------------------------------------------------------------------
def test_immutable_sources_are_never_due() -> None:
    # audit / whitepaper are immutable per version (KB_DESIGN §5 "Audit: forever").
    assert freshness_contract(refresh_cadence="immutable", source_type="audit") is None
    assert freshness_contract(refresh_cadence=None, source_type="audit") is None
    assert freshness_contract(refresh_cadence=None, source_type="whitepaper") is None
    # never due regardless of how old last_refreshed_at is
    assert (
        is_source_due(
            refresh_cadence=None,
            source_type="audit",
            last_refreshed_at=_iso(NOW - timedelta(days=3650)),
            now=NOW,
        )
        is False
    )


def test_contract_cadences_match_kb_design_table() -> None:
    # KB_DESIGN §5 explicit max-staleness windows.
    assert freshness_contract(refresh_cadence="hourly", source_type=None) == timedelta(hours=1)
    assert freshness_contract(refresh_cadence="daily", source_type=None) == timedelta(hours=24)
    assert freshness_contract(refresh_cadence="weekly", source_type=None) == timedelta(days=7)
    # source_type fallback when cadence absent
    assert freshness_contract(refresh_cadence=None, source_type="recent_tweet") == timedelta(hours=1)
    assert freshness_contract(refresh_cadence=None, source_type="docs") == timedelta(days=7)


def test_source_due_only_past_contract() -> None:
    # a daily source refreshed 2h ago is NOT due; refreshed 30h ago IS due.
    assert (
        is_source_due(
            refresh_cadence="daily",
            source_type="substack",
            last_refreshed_at=_iso(NOW - timedelta(hours=2)),
            now=NOW,
        )
        is False
    )
    assert (
        is_source_due(
            refresh_cadence="daily",
            source_type="substack",
            last_refreshed_at=_iso(NOW - timedelta(hours=30)),
            now=NOW,
        )
        is True
    )


def test_never_refreshed_source_is_due() -> None:
    # NULL last_refreshed_at on a time-bounded source → due (no fresh content yet).
    assert (
        is_source_due(
            refresh_cadence="weekly",
            source_type="docs",
            last_refreshed_at=None,
            now=NOW,
        )
        is True
    )


# ---------------------------------------------------------------------------
# authority-tiered ranking OVER the C3.2a fused set (KB_DESIGN §3 step 3)
# ---------------------------------------------------------------------------
def test_authority_weighting_reorders_fused_set() -> None:
    # Two chunks with the SAME fused (RRF) score; the higher-authority one must
    # rank first AFTER the §3-step-3 authority weighting (applied over fusion).
    low = KBChunk(chunk_id=1, client_id=1, text="docs answer", authority=0.5, source_type="docs", score=0.10)
    high = KBChunk(chunk_id=2, client_id=1, text="audit answer", authority=1.0, source_type="audit", score=0.10)
    ranked = rank_chunks([low, high], "what is the contract address", now=NOW)
    assert [r.chunk.chunk_id for r in ranked] == [2, 1]
    assert ranked[0].authority == 1.0


def test_authority_weighting_does_not_override_a_large_fusion_gap() -> None:
    # Authority BOOSTS but does not steamroll a decisively-better fused match: a
    # far-stronger fused score on the lower-authority chunk still wins.
    strong_low = KBChunk(chunk_id=1, client_id=1, text="x", authority=0.5, source_type="docs", score=0.9)
    weak_high = KBChunk(chunk_id=2, client_id=1, text="y", authority=1.0, source_type="audit", score=0.01)
    ranked = rank_chunks([strong_low, weak_high], "explain the mechanics", now=NOW)
    assert ranked[0].chunk.chunk_id == 1


# ---------------------------------------------------------------------------
# recency weighting on time-sensitive queries (KB_DESIGN §3 step 4)
# ---------------------------------------------------------------------------
def test_is_time_sensitive_detects_status_queries() -> None:
    assert is_time_sensitive("what is the current TVL")
    assert is_time_sensitive("when was the last buyback")
    assert is_time_sensitive("what's the status of the audit")
    assert not is_time_sensitive("how does the vault mechanism work")


def test_is_time_sensitive_matches_cues_on_word_boundaries() -> None:
    # Single-word cues must NOT match inside larger words (substring collisions):
    # "now" inside "knows", "live" inside "liveness"/"delivery", "current" in a
    # multi-word phrase that doesn't ask for a live fact.
    assert not is_time_sensitive("who knows the team")
    assert not is_time_sensitive("the renowned founder")
    assert not is_time_sensitive("show liveness")
    assert not is_time_sensitive("how does delivery work")
    # but the standalone cue tokens + multi-word phrases still trigger
    assert is_time_sensitive("is it live right now")
    assert is_time_sensitive("what is the tvl as of today")
    assert is_time_sensitive("is the doc up to date")


def test_recency_boosts_fresh_chunk_only_when_time_sensitive() -> None:
    fresh = KBChunk(chunk_id=1, client_id=1, text="tvl is 5M", authority=0.7, source_type="recent_tweet", score=0.10)
    old = KBChunk(chunk_id=2, client_id=1, text="tvl was 3M", authority=0.7, source_type="recent_tweet", score=0.10)
    indexed = {1: _iso(NOW - timedelta(hours=1)), 2: _iso(NOW - timedelta(days=60))}

    # time-sensitive: the FRESH chunk wins on the recency leg (same authority+fused)
    ranked = rank_chunks([old, fresh], "what is the current tvl", now=NOW, indexed_at_by_id=indexed)
    assert ranked[0].chunk.chunk_id == 1
    assert ranked[0].recency_boost > ranked[1].recency_boost

    # NOT time-sensitive: recency is not applied, the tie falls back to chunk id
    ranked2 = rank_chunks([old, fresh], "how is tvl computed", now=NOW, indexed_at_by_id=indexed)
    assert all(r.recency_boost == 0.0 for r in ranked2)


# ---------------------------------------------------------------------------
# end-to-end: seeded RM KB returns authority-ranked chunks over a real store
# ---------------------------------------------------------------------------
def test_seeded_kb_returns_authority_ranked_chunks(kb) -> None:
    conn, org_id, client_id = kb
    store = SQLiteKBStore(conn, FakeEmbeddingProvider())
    # two sources for the SAME fact at different authority tiers.
    audit_src = _seed_source(conn, client_id, source_type="audit", authority=1.0)
    docs_src = _seed_source(conn, client_id, source_type="docs", authority=0.5)
    store.index_source(
        org_id=org_id, client_id=client_id, source_id=audit_src,
        body="the robotmoney vault buyback uses treasury capital audited result",
        source_type="audit", authority=1.0,
    )
    store.index_source(
        org_id=org_id, client_id=client_id, source_id=docs_src,
        body="the robotmoney vault buyback uses treasury capital docs summary",
        source_type="docs", authority=0.5,
    )
    conn.commit()

    ranked = search_and_rank(conn, store, client_id, "how does the vault buyback work", now=NOW, top_k=2)
    assert ranked, "expected hits"
    # the higher-authority (audit, 1.0) chunk ranks first after §3 weighting
    assert ranked[0].chunk.source_type == "audit"
    assert ranked[0].authority == 1.0


def test_search_and_rank_boosts_fresh_chunk_end_to_end(kb) -> None:
    # E2E recency (KB_DESIGN §3 step 4) through search_and_rank: two same-authority
    # chunks for the SAME fact with DISTINCT indexed_at; a time-sensitive query must
    # boost the fresher one to rank 0. This closes the e2e gap left by the unit-only
    # recency test (rank_chunks alone) — search_and_rank loads indexed_at from the DB
    # via _load_indexed_at, so a wrong column / regressed time_sensitive branch is
    # caught here even though the rank_chunks unit test would still pass.
    conn, org_id, client_id = kb
    store = SQLiteKBStore(conn, FakeEmbeddingProvider())
    src = _seed_source(conn, client_id, source_type="recent_tweet", authority=0.7)
    body = "the current tvl figure is reported in the latest treasury update"
    fresh_id = _insert_chunk(
        conn, source_id=src, client_id=client_id, body=body,
        authority=0.7, indexed_at=_iso(NOW - timedelta(hours=1)),
    )
    stale_id = _insert_chunk(
        conn, source_id=src, client_id=client_id, body=body,
        authority=0.7, indexed_at=_iso(NOW - timedelta(days=60)),
    )
    # mirror the store's FTS5 companion maintenance for the directly-inserted rows
    for cid in (fresh_id, stale_id):
        conn.execute(
            text("INSERT INTO autocm_kb_chunks_fts (rowid, chunk_text) VALUES (:r, :t)"),
            {"r": cid, "t": body},
        )
    conn.commit()

    ranked = search_and_rank(conn, store, client_id, "what is the current tvl", now=NOW, top_k=2)
    assert [r.chunk.chunk_id for r in ranked] == [fresh_id, stale_id]
    assert ranked[0].recency_boost > ranked[1].recency_boost


# ---------------------------------------------------------------------------
# freshness-contract gate: stale cited chunk → HITL downgrade (KB_DESIGN §5)
# ---------------------------------------------------------------------------
def test_stale_cited_chunk_downgrades_to_hitl(kb) -> None:
    conn, org_id, client_id = kb
    # a recent-tweet source (1h contract) last refreshed 5h ago → stale.
    stale_src = _seed_source(
        conn, client_id, source_type="recent_tweet", authority=0.7,
        refresh_cadence="hourly", last_refreshed_at=_iso(NOW - timedelta(hours=5)),
    )
    stale_chunk = _insert_chunk(
        conn, source_id=stale_src, client_id=client_id,
        body="tvl is 5M as of an hour ago", authority=0.7,
        indexed_at=_iso(NOW - timedelta(hours=5)),
    )
    conn.commit()

    verdict = check_cited_freshness(conn, [stale_chunk], now=NOW)
    assert verdict.downgrade_to_hitl is True
    assert verdict.stale and verdict.stale[0].chunk_id == stale_chunk
    assert verdict.stale[0].source_type == "recent_tweet"


def test_fresh_cited_chunk_does_not_downgrade(kb) -> None:
    conn, org_id, client_id = kb
    # daily source refreshed 2h ago → within contract → fresh.
    fresh_src = _seed_source(
        conn, client_id, source_type="substack", authority=0.8,
        refresh_cadence="daily", last_refreshed_at=_iso(NOW - timedelta(hours=2)),
    )
    fresh_chunk = _insert_chunk(
        conn, source_id=fresh_src, client_id=client_id,
        body="latest substack thesis", authority=0.8,
        indexed_at=_iso(NOW - timedelta(hours=2)),
    )
    conn.commit()
    verdict = check_cited_freshness(conn, [fresh_chunk], now=NOW)
    assert verdict.downgrade_to_hitl is False
    assert verdict.stale == []


def test_immutable_cited_chunk_never_downgrades(kb) -> None:
    conn, org_id, client_id = kb
    # an audit chunk indexed years ago is STILL fresh (immutable contract).
    audit_src = _seed_source(
        conn, client_id, source_type="audit", authority=1.0,
        refresh_cadence="immutable",
        last_refreshed_at=_iso(NOW - timedelta(days=900)),
    )
    audit_chunk = _insert_chunk(
        conn, source_id=audit_src, client_id=client_id,
        body="the audit confirmed no reentrancy", authority=1.0,
        indexed_at=_iso(NOW - timedelta(days=900)),
    )
    conn.commit()
    verdict = check_cited_freshness(conn, [audit_chunk], now=NOW)
    assert verdict.downgrade_to_hitl is False


def test_empty_citation_list_is_fresh(kb) -> None:
    conn, org_id, client_id = kb
    assert check_cited_freshness(conn, [], now=NOW).downgrade_to_hitl is False


def test_gate_ignores_out_of_scope_client_chunk(kb) -> None:
    # KB_DESIGN §6 isolation: a chunk belonging to a DIFFERENT client, passed with
    # the in-scope client_id, is silently ignored (never gated against another
    # client's source). Without client_id the same id is processed (back-compat).
    conn, org_id, client_a = kb
    # a SECOND AutoCM client under its OWN org (autocm_clients.org_id is UNIQUE +
    # FKs to orgs — one client per org). Isolation is per client_id; client_b is a
    # different tenant than client_a.
    org_b = org_id + "_b"
    conn.execute(
        text("INSERT INTO orgs (org_id, display_name) VALUES (:o, 'Org B')"),
        {"o": org_b},
    )
    client_b = _seed_client(conn, org_b)
    # client B owns a stale recent_tweet chunk
    b_src = _seed_source(
        conn, client_b, source_type="recent_tweet", authority=0.7,
        refresh_cadence="hourly", last_refreshed_at=_iso(NOW - timedelta(hours=8)),
    )
    b_chunk = _insert_chunk(
        conn, source_id=b_src, client_id=client_b, body="client B stale fact",
        authority=0.7, indexed_at=_iso(NOW - timedelta(hours=8)),
    )
    conn.commit()

    # scoped to client A: B's chunk is out of scope → ignored → fresh verdict
    scoped = check_cited_freshness(conn, [b_chunk], now=NOW, client_id=client_a)
    assert scoped.downgrade_to_hitl is False
    assert scoped.stale == []
    # unscoped (no client_id): the chunk is processed and flagged stale
    unscoped = check_cited_freshness(conn, [b_chunk], now=NOW)
    assert unscoped.downgrade_to_hitl is True


def test_mixed_citations_one_stale_downgrades(kb) -> None:
    conn, org_id, client_id = kb
    fresh_src = _seed_source(
        conn, client_id, source_type="audit", authority=1.0,
        refresh_cadence="immutable", last_refreshed_at=_iso(NOW - timedelta(days=10)),
    )
    fresh_chunk = _insert_chunk(
        conn, source_id=fresh_src, client_id=client_id, body="audited fact",
        authority=1.0, indexed_at=_iso(NOW - timedelta(days=10)),
    )
    stale_src = _seed_source(
        conn, client_id, source_type="recent_tweet", authority=0.7,
        refresh_cadence="hourly", last_refreshed_at=_iso(NOW - timedelta(hours=8)),
    )
    stale_chunk = _insert_chunk(
        conn, source_id=stale_src, client_id=client_id, body="stale tweet fact",
        authority=0.7, indexed_at=_iso(NOW - timedelta(hours=8)),
    )
    conn.commit()
    verdict = check_cited_freshness(conn, [fresh_chunk, stale_chunk], now=NOW)
    assert verdict.downgrade_to_hitl is True
    # only the stale one is flagged
    assert {s.chunk_id for s in verdict.stale} == {stale_chunk}


# ---------------------------------------------------------------------------
# scheduled refresher with a FAKE CLOCK (no wall-clock dependency)
# ---------------------------------------------------------------------------
def _fake_clock(cell):
    return lambda: cell["t"]


def test_refresher_refreshes_only_due_sources(kb) -> None:
    conn, org_id, client_id = kb
    cell = {"t": NOW}
    # DUE: a docs source (7d contract) last refreshed 10d ago, with new content.
    due_src = _seed_source(
        conn, client_id, source_type="docs", authority=0.8,
        refresh_cadence="weekly", last_refreshed_at=_iso(NOW - timedelta(days=10)),
        source_url="https://rm.example/docs",
        fetch_config={},
    )
    # NOT DUE: a docs source refreshed 1d ago.
    fresh_src = _seed_source(
        conn, client_id, source_type="docs", authority=0.8,
        refresh_cadence="weekly", last_refreshed_at=_iso(NOW - timedelta(days=1)),
        source_url="https://rm.example/fresh",
    )
    # IMMUTABLE: audit, decades old — never due.
    audit_src = _seed_source(
        conn, client_id, source_type="audit", authority=1.0,
        refresh_cadence="immutable", last_refreshed_at=_iso(NOW - timedelta(days=900)),
    )
    conn.commit()

    fetcher = FakeHttpFetcher({"https://rm.example/docs": "<p>fresh vault docs content here</p>"})
    store = SQLiteKBStore(conn, FakeEmbeddingProvider())
    refresher = KBRefresher(
        conn, store, KBExtractor(fetcher), org_id=org_id, clock=_fake_clock(cell)
    )

    due = refresher.due_sources(client_id)
    assert due == [due_src]  # ONLY the stale docs source
    assert fresh_src not in due and audit_src not in due

    refreshed = refresher.refresh_client(client_id)
    assert refreshed == 1
    conn.commit()

    # the due source got new chunks + a bumped last_refreshed_at == NOW
    row = conn.execute(
        text("SELECT last_refreshed_at FROM autocm_kb_sources WHERE id = :id"),
        {"id": due_src},
    ).fetchone()
    assert row[0] == _iso(NOW)
    chunk_ct = conn.execute(
        text("SELECT COUNT(*) FROM autocm_kb_chunks WHERE source_id = :s AND status = 'active'"),
        {"s": due_src},
    ).fetchone()[0]
    assert chunk_ct >= 1


def test_refresher_becomes_due_as_clock_advances(kb) -> None:
    conn, org_id, client_id = kb
    cell = {"t": NOW}
    src = _seed_source(
        conn, client_id, source_type="substack", authority=0.8,
        refresh_cadence="daily", last_refreshed_at=_iso(NOW - timedelta(hours=2)),
        source_url="https://rm.example/sub",
    )
    conn.commit()
    fetcher = FakeHttpFetcher({"https://rm.example/sub": "<p>x</p>"})
    refresher = KBRefresher(
        conn, SQLiteKBStore(conn, FakeEmbeddingProvider()),
        KBExtractor(fetcher), org_id=org_id, clock=_fake_clock(cell),
    )
    # at NOW (2h old, daily contract) → not due
    assert refresher.due_sources(client_id) == []
    # advance the fake clock past the 24h contract
    cell["t"] = NOW + timedelta(hours=25)
    assert refresher.due_sources(client_id) == [src]


def test_refresher_unchanged_content_not_reindexed(kb) -> None:
    conn, org_id, client_id = kb
    cell = {"t": NOW}
    src = _seed_source(
        conn, client_id, source_type="docs", authority=0.8,
        refresh_cadence="weekly", last_refreshed_at=_iso(NOW - timedelta(days=10)),
        source_url="https://rm.example/docs",
    )
    conn.commit()
    fetcher = FakeHttpFetcher({"https://rm.example/docs": "<p>stable content body</p>"})
    store = SQLiteKBStore(conn, FakeEmbeddingProvider())
    refresher = KBRefresher(
        conn, store, KBExtractor(fetcher), org_id=org_id, clock=_fake_clock(cell)
    )
    # first sweep: indexes the content
    out1 = refresher.refresh_source(src)
    conn.commit()
    assert out1.changed is True and out1.new_chunk_ids

    # advance clock so it's due again, same content → NOT re-indexed (changed=False)
    cell["t"] = NOW + timedelta(days=8)
    out2 = refresher.refresh_source(src)
    conn.commit()
    assert out2.changed is False and out2.new_chunk_ids == []
    # only one active chunk-set (the first); no duplicate rows
    active = conn.execute(
        text("SELECT COUNT(*) FROM autocm_kb_chunks WHERE source_id = :s AND status = 'active'"),
        {"s": src},
    ).fetchone()[0]
    assert active == len(out1.new_chunk_ids)


def test_refresher_changed_content_supersedes_old_chunks(kb) -> None:
    conn, org_id, client_id = kb
    cell = {"t": NOW}
    src = _seed_source(
        conn, client_id, source_type="docs", authority=0.8,
        refresh_cadence="weekly", last_refreshed_at=_iso(NOW - timedelta(days=10)),
        source_url="https://rm.example/docs",
    )
    conn.commit()
    fetcher = FakeHttpFetcher({"https://rm.example/docs": "<p>version one content</p>"})
    store = SQLiteKBStore(conn, FakeEmbeddingProvider())
    refresher = KBRefresher(
        conn, store, KBExtractor(fetcher), org_id=org_id, clock=_fake_clock(cell)
    )
    out1 = refresher.refresh_source(src)
    conn.commit()
    old_ids = out1.new_chunk_ids

    # source content changed; advance clock; re-sweep
    fetcher.set("https://rm.example/docs", "<p>version two totally different content</p>")
    cell["t"] = NOW + timedelta(days=8)
    out2 = refresher.refresh_source(src)
    conn.commit()
    assert out2.changed is True and out2.new_chunk_ids
    # old chunks marked stale (KB_DESIGN §9), new chunks active
    old_status = conn.execute(
        text("SELECT status FROM autocm_kb_chunks WHERE id = :id"), {"id": old_ids[0]}
    ).fetchone()[0]
    assert old_status == "stale"


def test_refresher_dead_source_does_not_crash_sweep(kb) -> None:
    conn, org_id, client_id = kb
    cell = {"t": NOW}
    # dead URL (FakeHttpFetcher returns None) → no chunks, but the sweep proceeds.
    dead = _seed_source(
        conn, client_id, source_type="docs", authority=0.8,
        refresh_cadence="weekly", last_refreshed_at=_iso(NOW - timedelta(days=10)),
        source_url="https://rm.example/dead",
    )
    conn.commit()
    fetcher = FakeHttpFetcher({})  # no responses seeded → dead
    refresher = KBRefresher(
        conn, SQLiteKBStore(conn, FakeEmbeddingProvider()),
        KBExtractor(fetcher), org_id=org_id, clock=_fake_clock(cell),
    )
    refreshed = refresher.refresh_client(client_id)
    conn.commit()
    # the source was visited (last_refreshed_at bumped) without raising
    assert refreshed == 1
    row = conn.execute(
        text("SELECT last_refreshed_at FROM autocm_kb_sources WHERE id = :id"),
        {"id": dead},
    ).fetchone()
    assert row[0] == _iso(NOW)


def test_refresher_writes_audit_row(kb) -> None:
    conn, org_id, client_id = kb
    cell = {"t": NOW}
    src = _seed_source(
        conn, client_id, source_type="docs", authority=0.8,
        refresh_cadence="weekly", last_refreshed_at=_iso(NOW - timedelta(days=10)),
        source_url="https://rm.example/docs",
    )
    conn.commit()
    fetcher = FakeHttpFetcher({"https://rm.example/docs": "<p>content</p>"})
    refresher = KBRefresher(
        conn, SQLiteKBStore(conn, FakeEmbeddingProvider()),
        KBExtractor(fetcher), org_id=org_id, clock=_fake_clock(cell),
    )
    refresher.refresh_source(src)
    conn.commit()
    rows = conn.execute(
        text(
            "SELECT action, source, org_id FROM audit_log "
            "WHERE action = 'kb_source_refreshed' AND org_id = :o"
        ),
        {"o": org_id},
    ).fetchall()
    assert rows and rows[0][1] == "sable-autocm"


# ---------------------------------------------------------------------------
# resolved-FAQ → KB promotion write handler (synthetic interaction; no C3.7)
# ---------------------------------------------------------------------------
def _seed_interaction(
    conn, client_id, *, action="approve_for_kb", payload=None, target_ref=None
) -> int:
    row = conn.execute(
        text(
            "INSERT INTO autocm_digest_interactions "
            "(client_id, digest_period, section, action, target_ref, payload, actor) "
            "VALUES (:c, '2026-W22', 'top_questions', :a, :tr, :p, 'founder') "
            "RETURNING id"
        ),
        {
            "c": client_id,
            "a": action,
            "tr": target_ref,
            "p": json.dumps(payload or {}),
        },
    ).fetchone()
    return int(row[0])


def test_promote_resolved_faq_writes_canonical_chunk(kb) -> None:
    conn, org_id, client_id = kb
    interaction_id = _seed_interaction(
        conn, client_id,
        target_ref="how do I buy the token?",
        payload={"chunk_text": "Buy RM on the DEX at the official contract address."},
    )
    conn.commit()

    result = promote_resolved_faq(conn, interaction_id, actor="founder", org_id=org_id)
    conn.commit()

    # a high-authority (0.8) resolved_faq chunk now exists for the client
    row = conn.execute(
        text(
            "SELECT c.chunk_text, c.chunk_authority, c.status, s.source_type "
            "FROM autocm_kb_chunks c JOIN autocm_kb_sources s ON s.id = c.source_id "
            "WHERE c.id = :id"
        ),
        {"id": result.chunk_id},
    ).fetchone()
    assert row is not None
    assert "official contract address" in row[0]
    assert row[1] == RESOLVED_FAQ_AUTHORITY == 0.8
    assert row[2] == "active"
    assert row[3] == RESOLVED_FAQ_SOURCE_TYPE == "resolved_faq"


def test_promoted_chunk_is_retrievable_and_high_authority(kb) -> None:
    conn, org_id, client_id = kb
    interaction_id = _seed_interaction(
        conn, client_id,
        target_ref="what is the buyback cadence?",
        payload={"chunk_text": "The vault executes a treasury buyback every week."},
    )
    conn.commit()
    promote_resolved_faq(conn, interaction_id, actor="founder", org_id=org_id)
    conn.commit()

    # the canonical chunk is hybrid-retrievable AND ranks at 0.8 authority
    store = SQLiteKBStore(conn, FakeEmbeddingProvider())
    ranked = search_and_rank(conn, store, client_id, "tell me about the treasury buyback", now=NOW, top_k=3)
    assert ranked
    assert any(r.chunk.source_type == "resolved_faq" and r.authority == 0.8 for r in ranked)


def test_promote_writes_audit_row(kb) -> None:
    conn, org_id, client_id = kb
    interaction_id = _seed_interaction(
        conn, client_id, payload={"chunk_text": "canonical answer"}
    )
    conn.commit()
    promote_resolved_faq(conn, interaction_id, actor="founder", org_id=org_id)
    conn.commit()
    rows = conn.execute(
        text(
            "SELECT action, source FROM audit_log "
            "WHERE action = 'kb_resolved_faq_promoted' AND org_id = :o"
        ),
        {"o": org_id},
    ).fetchall()
    assert rows and rows[0][1] == "sable-autocm"


def test_promote_one_source_per_client(kb) -> None:
    conn, org_id, client_id = kb
    i1 = _seed_interaction(conn, client_id, payload={"chunk_text": "answer one"})
    i2 = _seed_interaction(conn, client_id, payload={"chunk_text": "answer two"})
    conn.commit()
    r1 = promote_resolved_faq(conn, i1, actor="founder", org_id=org_id)
    r2 = promote_resolved_faq(conn, i2, actor="founder", org_id=org_id)
    conn.commit()
    # both promotions share ONE resolved_faq source row for the client
    assert r1.source_id == r2.source_id
    src_count = conn.execute(
        text(
            "SELECT COUNT(*) FROM autocm_kb_sources "
            "WHERE client_id = :c AND source_type = 'resolved_faq'"
        ),
        {"c": client_id},
    ).fetchone()[0]
    assert src_count == 1


def test_promote_rejects_non_approve_action(kb) -> None:
    conn, org_id, client_id = kb
    interaction_id = _seed_interaction(
        conn, client_id, action="ignore", payload={"chunk_text": "x"}
    )
    conn.commit()
    with pytest.raises(ValueError):
        promote_resolved_faq(conn, interaction_id, actor="founder", org_id=org_id)


def test_promote_rejects_empty_payload(kb) -> None:
    conn, org_id, client_id = kb
    interaction_id = _seed_interaction(conn, client_id, payload={})
    conn.commit()
    with pytest.raises(ValueError):
        promote_resolved_faq(conn, interaction_id, actor="founder", org_id=org_id)


def test_promote_missing_interaction_raises(kb) -> None:
    conn, org_id, client_id = kb
    with pytest.raises(ValueError):
        promote_resolved_faq(conn, 999999, actor="founder", org_id=org_id)
