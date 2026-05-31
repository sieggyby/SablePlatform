"""KB source extractor (C3.2b) — web / RSS / structured-doc → normalized chunks.

Turns a configured ``autocm_kb_sources`` row into normalized text chunks ready for
the C3.2a :class:`~sable_platform.autocm.kb.store.SQLiteKBStore` write pipeline.
Three source families per ``KB_DESIGN §1``:

  * **web**  — project website / docs / scraped pages (authority 0.8 / 0.7).
    HTML is stripped to text with the stdlib ``html.parser`` (no bs4 dependency).
  * **rss**  — substack / blog feeds (authority 0.8 / 0.5). Parsed with the stdlib
    ``xml.etree.ElementTree`` (no feedparser dependency); supports RSS 2.0
    ``<item>`` and Atom ``<entry>`` shapes.
  * **doc**  — a structured document supplied inline in ``fetch_config`` (already
    plain text: pasted whitepaper / pinned-message text / manual transcript). No
    fetch — the text is chunked directly.

NETWORK ISOLATION FOR TESTS: all HTTP goes through the :class:`HttpFetcher` seam.
Production uses :class:`HttpxFetcher` (sync ``httpx.Client``); tests inject a
:class:`FakeHttpFetcher` keyed by URL — NO real network in the test suite (mirrors
the D-2 ``EmbeddingProvider`` fake/real split in ``kb.store``).

Chunking reuses ``kb.store.chunk_text`` (pinned ~512/64, ``KB_DESIGN §10``) so the
extractor and the store agree on chunk boundaries.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, List, Optional, Protocol
from xml.etree import ElementTree as ET

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .store import SQLiteKBStore, chunk_text

logger = logging.getLogger(__name__)

# source_type values this module knows how to extract. A source whose type is not
# one of these (e.g. ``onchain``, ``constant``, ``resolved_faq``) is not handled
# here — extract_source raises so a misrouted source fails loudly.
WEB_TYPES = frozenset({"web", "website", "docs", "doc_html"})
RSS_TYPES = frozenset({"rss", "substack", "feed", "atom"})
DOC_TYPES = frozenset({"doc", "structured_doc", "transcript", "pinned", "manual"})


# ---------------------------------------------------------------------------
# HTTP seam (injectable; FakeHttpFetcher for tests — NO real network in tests)
# ---------------------------------------------------------------------------
class HttpFetcher(Protocol):
    """The fetch seam: a URL (+ optional headers) → response body text.

    Returns ``None`` on any transport failure (the extractor degrades to "no
    chunks" rather than raising — a dead source must not crash a refresh sweep).
    """

    def get(self, url: str, *, headers: Optional[Dict[str, str]] = None) -> Optional[str]:
        ...


class HttpxFetcher:
    """Production :class:`HttpFetcher` over a synchronous ``httpx.Client``.

    Lazy-imports httpx (the vendored core already depends on it) so importing this
    module never pulls a heavy client at import time. Non-200 / transport errors
    return ``None`` so the caller degrades gracefully.
    """

    def __init__(self, *, timeout: float = 20.0, user_agent: str = "sable-autocm-kb") -> None:
        self._timeout = timeout
        self._user_agent = user_agent

    def get(self, url: str, *, headers: Optional[Dict[str, str]] = None) -> Optional[str]:
        import httpx  # lazy — keeps module import light

        merged = {"User-Agent": self._user_agent}
        if headers:
            merged.update(headers)
        try:
            with httpx.Client(timeout=self._timeout, headers=merged) as client:
                resp = client.get(url)
            if resp.status_code == 200:
                return resp.text
            logger.warning("KB extractor fetch %s returned HTTP %s", url, resp.status_code)
        except httpx.HTTPError as exc:  # pragma: no cover - network failure path
            logger.warning("KB extractor fetch %s failed: %s", url, exc)
        return None


class FakeHttpFetcher:
    """Deterministic offline :class:`HttpFetcher` for tests — keyed by URL.

    Seed it with ``{url: body}``; ``get`` returns the seeded body or ``None`` for
    an unknown URL (modeling a dead/unreachable source). Records ``calls`` so a
    test can assert exactly which URLs were fetched.
    """

    def __init__(self, responses: Optional[Dict[str, str]] = None) -> None:
        self._responses = dict(responses or {})
        self.calls: List[str] = []

    def set(self, url: str, body: str) -> None:
        self._responses[url] = body

    def get(self, url: str, *, headers: Optional[Dict[str, str]] = None) -> Optional[str]:
        self.calls.append(url)
        return self._responses.get(url)


# ---------------------------------------------------------------------------
# HTML → text (stdlib html.parser; no bs4)
# ---------------------------------------------------------------------------
class _TextHTMLParser(HTMLParser):
    """Collect visible text from HTML, dropping ``<script>``/``<style>`` content."""

    _SKIP_TAGS = frozenset({"script", "style", "noscript", "head", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        return " ".join(self._parts)


def html_to_text(html: str) -> str:
    """Strip HTML tags to a normalized, whitespace-collapsed text string."""
    parser = _TextHTMLParser()
    try:
        parser.feed(html)
    except Exception:  # pragma: no cover - malformed HTML; fall back to tag-strip
        logger.debug("html.parser failed; falling back to regex tag-strip")
        return _collapse_ws(re.sub(r"<[^>]+>", " ", html))
    return _collapse_ws(parser.text())


def _collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# RSS / Atom → list[entry text] (stdlib ElementTree; no feedparser)
# ---------------------------------------------------------------------------
def _strip_ns(tag: str) -> str:
    """Drop an XML namespace prefix: ``{http://www.w3.org/2005/Atom}entry`` → ``entry``."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_feed(xml: str) -> List[str]:
    """Parse an RSS 2.0 or Atom feed → one normalized text block per item/entry.

    Each block is ``"<title>. <description-or-summary-or-content>"`` with HTML
    stripped from the body. Robust to namespaces (Atom) and to either RSS
    ``<item>`` or Atom ``<entry>`` element names. Returns ``[]`` on a parse error.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        logger.warning("KB extractor: feed XML parse error")
        return []
    blocks: List[str] = []
    for el in root.iter():
        if _strip_ns(el.tag) not in {"item", "entry"}:
            continue
        title = ""
        body = ""
        for child in el:
            name = _strip_ns(child.tag)
            ctext = (child.text or "").strip()
            if name == "title" and not title:
                title = ctext
            elif name in {"description", "summary", "content", "encoded"} and not body:
                body = ctext
        body_text = html_to_text(body) if "<" in body else _collapse_ws(body)
        combined = ". ".join(p for p in (title, body_text) if p)
        if combined:
            blocks.append(combined)
    return blocks


# ---------------------------------------------------------------------------
# Extractor protocol + concrete extractors
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExtractedSource:
    """The extractor's normalized output: chunks + the source row id they belong to."""

    source_id: int
    client_id: int
    source_type: str
    chunks: List[str] = field(default_factory=list)


class SourceExtractor(Protocol):
    """Turn a configured source (website/RSS/substack/doc) into raw text chunks."""

    def extract(self, source_config: dict) -> List[str]:
        """Return normalized text chunks for a single ``autocm_kb_sources`` row.

        ``source_config`` carries (at least) ``source_type`` and ``source_url``,
        plus the parsed ``fetch_config`` (``dict``) the concrete extractor needs.
        """
        ...


def _chunks_from_body(body: str) -> List[str]:
    """Normalize + chunk a plain-text body via the store's pinned ~512/64 chunker."""
    norm = _collapse_ws(body)
    if not norm:
        return []
    return chunk_text(norm)


class WebExtractor:
    """Scrape a single web URL → HTML-stripped, chunked text (``KB_DESIGN §1``)."""

    def __init__(self, fetcher: HttpFetcher) -> None:
        self._fetcher = fetcher

    def extract(self, source_config: dict) -> List[str]:
        url = source_config.get("source_url")
        if not url:
            logger.warning("web source %s has no source_url", source_config.get("id"))
            return []
        html = self._fetcher.get(url)
        if html is None:
            return []
        return _chunks_from_body(html_to_text(html))


class RSSExtractor:
    """Fetch + parse an RSS/Atom feed → one chunk-set per entry (``KB_DESIGN §1``).

    ``fetch_config.max_items`` (default 20) bounds how many recent entries are
    ingested so a long feed does not blow the chunk budget.
    """

    def __init__(self, fetcher: HttpFetcher) -> None:
        self._fetcher = fetcher

    def extract(self, source_config: dict) -> List[str]:
        url = source_config.get("source_url")
        if not url:
            logger.warning("rss source %s has no source_url", source_config.get("id"))
            return []
        xml = self._fetcher.get(url)
        if xml is None:
            return []
        fetch_config = source_config.get("fetch_config") or {}
        max_items = int(fetch_config.get("max_items", 20))
        entries = parse_feed(xml)[: max(max_items, 0)]
        out: List[str] = []
        for entry in entries:
            out.extend(_chunks_from_body(entry))
        return out


class StructuredDocExtractor:
    """Chunk an inline structured document (no fetch).

    The text lives in ``fetch_config.text`` — a pasted whitepaper section, a pinned
    message, a manual transcript (``KB_DESIGN §1`` manual / transcript / pinned
    rows). This is the offline-by-construction extractor: no HTTP at all.
    """

    def extract(self, source_config: dict) -> List[str]:
        fetch_config = source_config.get("fetch_config") or {}
        body = fetch_config.get("text") or ""
        return _chunks_from_body(body)


class KBExtractor:
    """Dispatch an ``autocm_kb_sources`` row to the right family extractor.

    One instance per refresh sweep — holds the shared :class:`HttpFetcher` (so the
    web + rss legs reuse one client) and the no-fetch doc extractor. The
    C3.2c refresher drives this; C3.2b ships the extraction + ``index_source`` glue.
    """

    def __init__(self, fetcher: Optional[HttpFetcher] = None) -> None:
        self._fetcher: HttpFetcher = fetcher or HttpxFetcher()
        self._web = WebExtractor(self._fetcher)
        self._rss = RSSExtractor(self._fetcher)
        self._doc = StructuredDocExtractor()

    def _for(self, source_type: str) -> SourceExtractor:
        st = source_type.strip().lower()
        if st in WEB_TYPES:
            return self._web
        if st in RSS_TYPES:
            return self._rss
        if st in DOC_TYPES:
            return self._doc
        raise ValueError(
            f"no KB extractor for source_type {source_type!r}; "
            f"expected one of web/rss/doc families"
        )

    def extract_config(self, source_config: dict) -> List[str]:
        """Extract chunks from an already-loaded source config dict."""
        return self._for(source_config["source_type"]).extract(source_config)

    def extract_source(self, conn: Connection, source_id: int) -> ExtractedSource:
        """Load an ``autocm_kb_sources`` row by id → extract its normalized chunks.

        Reads ``source_type`` / ``source_url`` / ``fetch_config`` (JSON-decoded) and
        dispatches to the family extractor. The row's ``client_id`` rides along so
        the caller can index the chunks under the right tenant scope.
        """
        row = conn.execute(
            text(
                "SELECT id, client_id, source_type, source_url, fetch_config "
                "FROM autocm_kb_sources WHERE id = :id"
            ),
            {"id": source_id},
        ).fetchone()
        if row is None:
            raise ValueError(f"autocm_kb_sources id {source_id} not found")
        import json

        try:
            fetch_config = json.loads(row[4] or "{}")
        except (TypeError, ValueError):
            fetch_config = {}
        source_config = {
            "id": int(row[0]),
            "client_id": int(row[1]),
            "source_type": row[2],
            "source_url": row[3],
            "fetch_config": fetch_config,
        }
        chunks = self.extract_config(source_config)
        return ExtractedSource(
            source_id=int(row[0]),
            client_id=int(row[1]),
            source_type=row[2],
            chunks=chunks,
        )

    def index_source(
        self,
        conn: Connection,
        store: SQLiteKBStore,
        *,
        org_id: str,
        source_id: int,
    ) -> List[int]:
        """Extract a source AND write its chunks through the C3.2a store.

        The store's ``index_source`` chunks again at the pinned ~512/64 params, so
        here each EXTRACTED block is indexed independently (the extractor's job is
        normalization + per-entry splitting; the store owns embed/index/FTS). The
        source's ``authority_default`` becomes each chunk's authority. Returns every
        new ``autocm_kb_chunks`` row id. A source that yields no chunks (dead URL,
        empty feed) returns ``[]`` without writing.
        """
        extracted = self.extract_source(conn, source_id)
        if not extracted.chunks:
            return []
        authority = conn.execute(
            text("SELECT authority_default FROM autocm_kb_sources WHERE id = :id"),
            {"id": source_id},
        ).fetchone()[0]
        ids: List[int] = []
        for block in extracted.chunks:
            ids.extend(
                store.index_source(
                    org_id=org_id,
                    client_id=extracted.client_id,
                    source_id=source_id,
                    body=block,
                    source_type=extracted.source_type,
                    authority=float(authority),
                )
            )
        return ids


class NotImplementedExtractor:
    """Stub extractor retained for callers not yet wired to a real extractor.

    Raises so accidental hot-path use is loud. The real path is :class:`KBExtractor`
    (+ the family extractors above).
    """

    def extract(self, source_config: dict) -> List[str]:
        raise NotImplementedError("use KBExtractor / WebExtractor / RSSExtractor (C3.2b)")


__all__ = [
    # dispatcher + glue
    "KBExtractor",
    "ExtractedSource",
    # family extractors
    "SourceExtractor",
    "WebExtractor",
    "RSSExtractor",
    "StructuredDocExtractor",
    "NotImplementedExtractor",
    # HTTP seam
    "HttpFetcher",
    "HttpxFetcher",
    "FakeHttpFetcher",
    # parsing helpers
    "html_to_text",
    "parse_feed",
    # source-type families
    "WEB_TYPES",
    "RSS_TYPES",
    "DOC_TYPES",
]
