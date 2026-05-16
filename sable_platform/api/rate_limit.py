"""Simple per-token + per-IP rate limiter.

In-memory token-bucket. State is per-process — the MVP runs as a single
process. If multi-worker becomes a requirement, swap this for a SQLite-
backed bucket (the platform's `platform_meta` table can hold the state).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class _Window:
    requests: deque = field(default_factory=deque)


@dataclass(frozen=True)
class RateLimitConfig:
    # Steady-state requests-per-minute per token, per IP. The reader scope
    # gets a higher cap because GETs are cheap.
    read_per_min_token: int = 60
    write_per_min_token: int = 20
    per_min_ip: int = 120


class RateLimiter:
    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self.config = config or RateLimitConfig()
        self._lock = threading.Lock()
        self._token_windows: dict[tuple[str, str], _Window] = {}
        self._ip_windows: dict[str, _Window] = {}

    def check(
        self,
        *,
        token_id: str,
        ip: str,
        scope_class: str,  # "read" or "write"
        now: float | None = None,
    ) -> tuple[bool, int]:
        """Returns ``(allowed, retry_after_seconds)``.

        retry_after is only meaningful when allowed=False.
        """
        now = now if now is not None else time.monotonic()
        cap = (
            self.config.read_per_min_token
            if scope_class == "read"
            else self.config.write_per_min_token
        )
        token_key = (token_id, scope_class)

        with self._lock:
            tw = self._token_windows.setdefault(token_key, _Window())
            iw = self._ip_windows.setdefault(ip, _Window())

            _prune(tw.requests, now)
            _prune(iw.requests, now)

            token_used = len(tw.requests)
            ip_used = len(iw.requests)

            if token_used >= cap:
                retry = int(60 - (now - tw.requests[0])) + 1
                return False, max(retry, 1)
            if ip_used >= self.config.per_min_ip:
                retry = int(60 - (now - iw.requests[0])) + 1
                return False, max(retry, 1)

            tw.requests.append(now)
            iw.requests.append(now)
            return True, 0


def _prune(window: deque, now: float) -> None:
    cutoff = now - 60.0
    while window and window[0] < cutoff:
        window.popleft()
