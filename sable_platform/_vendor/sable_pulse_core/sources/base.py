"""Source interfaces + data contracts. Implementations live alongside this file."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class MarketData:
    symbol: str
    name: str
    price_usd: float | None = None
    market_cap: float | None = None
    fdv: float | None = None
    volume_24h: float | None = None
    liquidity_usd: float | None = None
    price_change_24h: float | None = None
    pair_age_days: float | None = None
    chain: str | None = None
    address: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class CommitteeCall:
    date: str
    summary: str
    source_url: str
    subject: str | None = None
    regime: str | None = None
    composite: float | None = None
    disagreement: str | None = None
    stale: bool = False


@dataclass
class DevActivity:
    repo: str
    since: str
    commits: list[dict] = field(default_factory=list)
    pulls: list[dict] = field(default_factory=list)
    releases: list[dict] = field(default_factory=list)


class MarketSource(ABC):
    @abstractmethod
    async def fetch(self, query: str) -> MarketData | None: ...


class CommitteeSource(ABC):
    @abstractmethod
    async def latest(self) -> CommitteeCall | None: ...


class GitHubSource(ABC):
    @abstractmethod
    async def recent_activity(self) -> list[DevActivity]: ...
