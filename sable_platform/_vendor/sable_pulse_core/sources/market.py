"""MarketSource — Dexscreener (free, no key, ideal for Base-by-contract).

Generic across projects: give it a contract address or a ticker. Parsing is split
out as a pure staticmethod so it's unit-testable without a network call.
"""
from __future__ import annotations

import time

import httpx

from .base import MarketData, MarketSource

DEXSCREENER = "https://api.dexscreener.com/latest/dex"


class DexscreenerSource(MarketSource):
    def __init__(self, chain: str | None = None, timeout: float = 15.0):
        self.chain = chain
        self.timeout = timeout

    async def fetch(self, query: str) -> MarketData | None:
        query = query.strip().lstrip("$")
        is_addr = query.lower().startswith("0x") and len(query) >= 40
        url = f"{DEXSCREENER}/tokens/{query}" if is_addr else f"{DEXSCREENER}/search"
        params = None if is_addr else {"q": query}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
        pairs = data.get("pairs") or []
        if self.chain:
            pairs = [p for p in pairs if p.get("chainId") == self.chain] or pairs
        if not pairs:
            return None
        pair = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
        return self.parse(pair)

    @staticmethod
    def parse(pair: dict) -> MarketData:
        base = pair.get("baseToken", {}) or {}
        created_ms = pair.get("pairCreatedAt")
        age_days = (time.time() - created_ms / 1000) / 86_400 if created_ms else None

        def f(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        return MarketData(
            symbol=base.get("symbol", "?"),
            name=base.get("name") or base.get("symbol", "?"),
            price_usd=f(pair.get("priceUsd")),
            market_cap=f(pair.get("marketCap")),
            fdv=f(pair.get("fdv")),
            volume_24h=f((pair.get("volume") or {}).get("h24")),
            liquidity_usd=f((pair.get("liquidity") or {}).get("usd")),
            price_change_24h=f((pair.get("priceChange") or {}).get("h24")),
            pair_age_days=age_days,
            chain=pair.get("chainId"),
            address=base.get("address"),
            raw=pair,
        )
