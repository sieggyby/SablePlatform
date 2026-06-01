"""Stacked, in-memory rate limiter.

Per RobotMoney grill (2026-05-30): global 3/10min, per-account 1/10min AND 3/day,
whitelist bypass. `check()` is read-only; call `record()` only after a successful
command so denied/failed calls don't consume quota.

Single-process only — counters are module-instance state with no cross-process
invalidation. A second replica would let each process grant its own quota. Move
state to a shared store before horizontal scale (mirrors the sable-roles
single-process constraint note).
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass
class RateLimitConfig:
    global_max: int = 3
    global_window_s: int = 600
    per_account_per_window: int = 1
    per_account_window_s: int = 600
    per_account_daily_max: int = 3
    daily_window_s: int = 86_400


@dataclass
class RateLimitDecision:
    allowed: bool
    reason: str = "ok"
    retry_after_seconds: int = 0


class RateLimiter:
    def __init__(self, config: RateLimitConfig, whitelist: set[int] | None = None, clock=time.time):
        self.cfg = config
        self.whitelist = set(whitelist or set())
        self._clock = clock
        self._global: deque[float] = deque()
        self._account_recent: dict[int, deque[float]] = defaultdict(deque)
        self._account_daily: dict[int, deque[float]] = defaultdict(deque)

    @staticmethod
    def _prune(dq: deque[float], window: float, now: float) -> None:
        cutoff = now - window
        while dq and dq[0] <= cutoff:
            dq.popleft()

    def check(self, user_id: int) -> RateLimitDecision:
        """Would this user be allowed right now? Does NOT consume quota."""
        if user_id in self.whitelist:
            return RateLimitDecision(True, "whitelist")
        now = self._clock()

        daily = self._account_daily[user_id]
        self._prune(daily, self.cfg.daily_window_s, now)
        if len(daily) >= self.cfg.per_account_daily_max:
            return RateLimitDecision(False, "daily_cap", max(int(self.cfg.daily_window_s - (now - daily[0])), 1))

        recent = self._account_recent[user_id]
        self._prune(recent, self.cfg.per_account_window_s, now)
        if len(recent) >= self.cfg.per_account_per_window:
            return RateLimitDecision(False, "account_window", max(int(self.cfg.per_account_window_s - (now - recent[0])), 1))

        self._prune(self._global, self.cfg.global_window_s, now)
        if len(self._global) >= self.cfg.global_max:
            return RateLimitDecision(False, "global_window", max(int(self.cfg.global_window_s - (now - self._global[0])), 1))

        return RateLimitDecision(True, "ok")

    def record(self, user_id: int) -> None:
        """Consume one unit of quota across all three windows. No-op for whitelist."""
        if user_id in self.whitelist:
            return
        now = self._clock()
        self._global.append(now)
        self._account_recent[user_id].append(now)
        self._account_daily[user_id].append(now)
