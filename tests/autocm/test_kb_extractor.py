"""C3.2b — kb.extractor tests.

Exit-criterion coverage (MEGAPLAN C3.2b exit/audit + tests line):
  * extractor chunks a seeded web/RSS/doc source into normalized chunks ready for
    the C3.2a store;
  * the extractor → store glue indexes those chunks (round-trips through search).

All offline — FakeHttpFetcher, NO real network. (The onchain key-isolation
security property lives in test_kb_onchain.py.)
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from sable_platform.autocm.kb.extractor import (
    DOC_TYPES,
    RSS_TYPES,
    WEB_TYPES,
    FakeHttpFetcher,
    KBExtractor,
    RSSExtractor,
    StructuredDocExtractor,
    WebExtractor,
    html_to_text,
    parse_feed,
)
from sable_platform.autocm.kb.store import FakeEmbeddingProvider, SQLiteKBStore


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


def _seed_source(
    conn,
    client_id: int,
    *,
    source_type: str,
    source_url: str | None = None,
    fetch_config: dict | None = None,
    authority: float = 0.8,
) -> int:
    row = conn.execute(
        text(
            "INSERT INTO autocm_kb_sources "
            "(client_id, source_type, source_url, fetch_config, authority_default) "
            "VALUES (:c, :st, :u, :fc, :a) RETURNING id"
        ),
        {
            "c": client_id,
            "st": source_type,
            "u": source_url,
            "fc": json.dumps(fetch_config or {}),
            "a": authority,
        },
    ).fetchone()
    return int(row[0])


@pytest.fixture
def kb_env(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    conn.commit()
    return conn, org_id, client_id


# ---------------------------------------------------------------------------
# stdlib HTML → text (no bs4)
# ---------------------------------------------------------------------------
def test_html_to_text_strips_tags_and_scripts() -> None:
    html = (
        "<html><head><style>.x{color:red}</style></head>"
        "<body><h1>RobotMoney Vault</h1>"
        "<script>var a = 1;</script>"
        "<p>The vault deploys treasury capital.</p></body></html>"
    )
    out = html_to_text(html)
    assert "RobotMoney Vault" in out
    assert "The vault deploys treasury capital." in out
    # script + style content is dropped
    assert "color:red" not in out
    assert "var a" not in out


# ---------------------------------------------------------------------------
# stdlib RSS/Atom → entries (no feedparser)
# ---------------------------------------------------------------------------
def test_parse_feed_rss20() -> None:
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <title>RM Blog</title>
      <item><title>Buyback update</title>
        <description>The vault executed a buyback this week.</description></item>
      <item><title>TVL milestone</title>
        <description>TVL crossed a new high.</description></item>
    </channel></rss>"""
    entries = parse_feed(xml)
    assert len(entries) == 2
    assert "Buyback update" in entries[0]
    assert "The vault executed a buyback this week." in entries[0]
    assert "TVL milestone" in entries[1]


def test_parse_feed_atom_with_namespace() -> None:
    xml = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>RM Substack</title>
      <entry><title>Thesis post</title>
        <summary>Our long-form thesis on agentic capital.</summary></entry>
    </feed>"""
    entries = parse_feed(xml)
    assert len(entries) == 1
    assert "Thesis post" in entries[0]
    assert "agentic capital" in entries[0]


def test_parse_feed_strips_html_in_body() -> None:
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item><title>HTML body</title>
        <description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;</description></item>
    </channel></rss>"""
    entries = parse_feed(xml)
    assert entries == ["HTML body. Hello world"]


def test_parse_feed_bad_xml_returns_empty() -> None:
    assert parse_feed("not xml <<<") == []


# ---------------------------------------------------------------------------
# family extractors → normalized chunks
# ---------------------------------------------------------------------------
def test_web_extractor_chunks_scraped_page() -> None:
    fetcher = FakeHttpFetcher(
        {"https://robotmoney.net/docs": "<h1>Docs</h1><p>The vault buyback mechanism.</p>"}
    )
    ext = WebExtractor(fetcher)
    chunks = ext.extract(
        {"id": 1, "source_type": "web", "source_url": "https://robotmoney.net/docs"}
    )
    assert chunks
    assert "vault buyback mechanism" in chunks[0]
    assert fetcher.calls == ["https://robotmoney.net/docs"]


def test_web_extractor_dead_url_returns_empty() -> None:
    # unknown URL → fetcher returns None → degrade to no chunks (no raise)
    ext = WebExtractor(FakeHttpFetcher({}))
    assert ext.extract({"id": 1, "source_type": "web", "source_url": "https://dead.example"}) == []


def test_rss_extractor_chunks_each_entry() -> None:
    feed = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item><title>One</title><description>first post body</description></item>
      <item><title>Two</title><description>second post body</description></item>
    </channel></rss>"""
    fetcher = FakeHttpFetcher({"https://rm.substack.com/feed": feed})
    ext = RSSExtractor(fetcher)
    chunks = ext.extract(
        {"id": 2, "source_type": "rss", "source_url": "https://rm.substack.com/feed",
         "fetch_config": {}}
    )
    assert len(chunks) == 2
    assert "first post body" in chunks[0]
    assert "second post body" in chunks[1]


def test_rss_extractor_respects_max_items() -> None:
    items = "".join(
        f"<item><title>T{i}</title><description>body {i}</description></item>"
        for i in range(5)
    )
    feed = f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'
    fetcher = FakeHttpFetcher({"https://rm.substack.com/feed": feed})
    chunks = RSSExtractor(fetcher).extract(
        {"id": 2, "source_type": "rss", "source_url": "https://rm.substack.com/feed",
         "fetch_config": {"max_items": 2}}
    )
    assert len(chunks) == 2


def test_structured_doc_extractor_chunks_inline_text() -> None:
    ext = StructuredDocExtractor()
    chunks = ext.extract(
        {"id": 3, "source_type": "doc",
         "fetch_config": {"text": "The whitepaper section on tokenomics and emission."}}
    )
    assert chunks == ["The whitepaper section on tokenomics and emission."]


def test_structured_doc_empty_text_returns_empty() -> None:
    assert StructuredDocExtractor().extract({"id": 3, "source_type": "doc",
                                             "fetch_config": {}}) == []


# ---------------------------------------------------------------------------
# dispatcher (KBExtractor) — source_type → family
# ---------------------------------------------------------------------------
def test_dispatcher_routes_by_source_type() -> None:
    feed = ('<?xml version="1.0"?><rss version="2.0"><channel>'
            "<item><title>P</title><description>feed body</description></item>"
            "</channel></rss>")
    fetcher = FakeHttpFetcher({
        "https://site/page": "<p>web body</p>",
        "https://site/feed": feed,
    })
    kb = KBExtractor(fetcher)
    assert "web body" in kb.extract_config(
        {"source_type": "web", "source_url": "https://site/page"})[0]
    assert "feed body" in kb.extract_config(
        {"source_type": "rss", "source_url": "https://site/feed", "fetch_config": {}})[0]
    assert kb.extract_config(
        {"source_type": "doc", "fetch_config": {"text": "doc body"}})[0] == "doc body"


def test_dispatcher_unknown_type_raises() -> None:
    with pytest.raises(ValueError):
        KBExtractor(FakeHttpFetcher({})).extract_config({"source_type": "onchain"})


def test_source_type_families_cover_kb_design_types() -> None:
    # the three families named in KB_DESIGN §1 storage shapes
    assert "web" in WEB_TYPES and "substack" in RSS_TYPES and "doc" in DOC_TYPES


# ---------------------------------------------------------------------------
# extract_source: load an autocm_kb_sources row → chunks
# ---------------------------------------------------------------------------
def test_extract_source_loads_row_and_chunks_doc(kb_env) -> None:
    conn, org_id, client_id = kb_env
    source_id = _seed_source(
        conn, client_id, source_type="doc",
        fetch_config={"text": "Pinned message: contract is verified on Basescan."},
    )
    conn.commit()
    kb = KBExtractor(FakeHttpFetcher({}))
    extracted = kb.extract_source(conn, source_id)
    assert extracted.source_id == source_id
    assert extracted.client_id == client_id
    assert extracted.source_type == "doc"
    assert "verified on Basescan" in extracted.chunks[0]


def test_extract_source_loads_web_row(kb_env) -> None:
    conn, org_id, client_id = kb_env
    url = "https://robotmoney.net/about"
    source_id = _seed_source(conn, client_id, source_type="web", source_url=url)
    conn.commit()
    kb = KBExtractor(FakeHttpFetcher({url: "<h1>About</h1><p>Agentic treasury.</p>"}))
    extracted = kb.extract_source(conn, source_id)
    assert "Agentic treasury." in extracted.chunks[0]


def test_extract_source_missing_row_raises(kb_env) -> None:
    conn, _, _ = kb_env
    with pytest.raises(ValueError):
        KBExtractor(FakeHttpFetcher({})).extract_source(conn, 9999)


# ---------------------------------------------------------------------------
# extractor → store glue (the C3.2b "feeds kb.store" contract)
# ---------------------------------------------------------------------------
def test_index_source_writes_chunks_through_store_and_round_trips(kb_env) -> None:
    conn, org_id, client_id = kb_env
    url = "https://robotmoney.net/vault"
    source_id = _seed_source(
        conn, client_id, source_type="web", source_url=url, authority=0.8
    )
    conn.commit()

    fetcher = FakeHttpFetcher(
        {url: "<h1>Vault</h1><p>The vault buyback deploys treasury capital each week.</p>"}
    )
    kb = KBExtractor(fetcher)
    store = SQLiteKBStore(conn, FakeEmbeddingProvider())

    ids = kb.index_source(conn, store, org_id=org_id, source_id=source_id)
    conn.commit()
    assert ids, "extractor → store must write chunk rows"

    # the chunk carries the SOURCE's authority + source_type, and round-trips search
    results = store.search(client_id, "how does the vault buyback work", top_k=3)
    assert results
    assert any("buyback" in c.text for c in results)
    top = results[0]
    assert top.source_type == "web"
    assert top.authority == pytest.approx(0.8)


def test_index_source_dead_url_writes_nothing(kb_env) -> None:
    conn, org_id, client_id = kb_env
    source_id = _seed_source(
        conn, client_id, source_type="web", source_url="https://dead.example"
    )
    conn.commit()
    kb = KBExtractor(FakeHttpFetcher({}))  # URL not seeded → None
    store = SQLiteKBStore(conn, FakeEmbeddingProvider())
    assert kb.index_source(conn, store, org_id=org_id, source_id=source_id) == []
    # nothing indexed
    count = conn.execute(
        text("SELECT COUNT(*) FROM autocm_kb_chunks WHERE client_id = :c"),
        {"c": client_id},
    ).fetchone()[0]
    assert count == 0
