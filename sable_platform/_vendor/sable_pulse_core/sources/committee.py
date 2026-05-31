"""CommitteeSource — reads the PUBLIC committee page and relays the latest call.

RobotMoney's committee page is US-geoblocked and refuses normal fetchers, so we
read it through a reader proxy (r.jina.ai) and parse the latest dated entry. This
is a best-effort parser over a human-facing page, not an API — so we cache the
last-good parse and serve it (flagged stale) when a live fetch fails, and we
refresh at most once per `ttl_seconds` regardless of how many users call /committee
(so the proxy is never hammered).

Round 2: swap this for `RealFeedSource` behind the same `CommitteeSource`
interface once Lex provides a stable feed — removes the geoblock + parse fragility.
"""
from __future__ import annotations

import re
from dataclasses import asdict

import httpx

from ..cache import JsonCache
from .base import CommitteeCall, CommitteeSource

_DATE = re.compile(r"^#{0,6}\s*([A-Z][a-z]+\s+\d{1,2},\s*\d{4})\b(.*)$")
_REGIME = re.compile(r"regime[:*\s]+([A-Za-z\-]+)", re.I)
_COMPOSITE = re.compile(r"composite[^0-9]*([01]\.\d+)", re.I)
_DISAGREE = re.compile(r"[^.]*\b(disagree|diverge|contested|unresolved|core disagreement)\b[^.]*\.", re.I)


class PublicPageCommitteeSource(CommitteeSource):
    def __init__(
        self,
        url: str,
        reader_proxy: str = "https://r.jina.ai/",
        cache: JsonCache | None = None,
        timeout: float = 25.0,
    ):
        self.url = url
        self.reader_proxy = reader_proxy.rstrip("/") + "/"
        self.cache = cache
        self.timeout = timeout

    async def latest(self) -> CommitteeCall | None:
        # Serve fresh cache without touching the network (global refresh throttle).
        if self.cache:
            entry = self.cache.get("committee_latest")
            if entry and not entry.stale:
                return CommitteeCall(**entry.value)
        try:
            text = await self._fetch()
            call = self.parse(text, self.url)
            if call and self.cache:
                self.cache.set("committee_latest", asdict(call))
            return call
        except Exception:
            if self.cache:
                entry = self.cache.get("committee_latest")
                if entry:
                    data = dict(entry.value)
                    data["stale"] = True
                    return CommitteeCall(**data)
            return None

    async def _fetch(self) -> str:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            r = await client.get(f"{self.reader_proxy}{self.url}", headers={"User-Agent": "sable-pulse/0.1"})
            r.raise_for_status()
            return r.text

    @staticmethod
    def parse(text: str, source_url: str) -> CommitteeCall | None:
        lines = text.splitlines()
        start = None
        date = subject = None
        for i, line in enumerate(lines):
            m = _DATE.match(line.strip())
            if m:
                start, date, subject = i, m.group(1).strip(), (m.group(2) or "").strip(" -—·")
                break
        if start is None:
            return None
        block: list[str] = []
        for line in lines[start + 1:]:
            if _DATE.match(line.strip()):
                break
            block.append(line)
        body = "\n".join(block).strip()
        regime_m = _REGIME.search(body)
        comp_m = _COMPOSITE.search(body)
        disagree_m = _DISAGREE.search(body)
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip() and not _REGIME.match(p.strip())]
        summary = " ".join(paras[:1]) if paras else (subject or "")
        return CommitteeCall(
            date=date or "",
            summary=summary[:700],
            source_url=source_url,
            subject=subject or None,
            regime=regime_m.group(1).strip() if regime_m else None,
            composite=float(comp_m.group(1)) if comp_m else None,
            disagreement=disagree_m.group(0).strip() if disagree_m else None,
        )
