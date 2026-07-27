"""SocialData prepaid-balance guard.

SocialData is PREPAID and shared by every X-dependent system in the suite — the relay
sweep, Cult Grader, SableKOL, and (soon) the community audit. Unlike Anthropic, which
is postpaid and bounded by our own caps, hitting zero here returns HTTP 402 and breaks
all of them at once, including tooling paying clients depend on. It has happened:
the balance hit $0 on 2026-05-07 mid-SolStitch.

This module exists so cheap, speculative, free-tier work (a prospect's audit) can be
made to fail BEFORE it starves paid delivery.

Billing note: SocialData bills per OBJECT RETURNED, not per request — 1 credit = $0.01,
and a returned profile/tweet/follower costs 0.02 credits ($0.0002). A 20-result page is
therefore ~$0.004, roughly 2x the old per-call estimate the cost ledger used to assume.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Literal

log = logging.getLogger(__name__)

BALANCE_URL = "https://api.socialdata.tools/user"  # bare /user — /me, /account, /user/me all 404
_CREDITS_PER_USD = 100.0

#: Below this, free-tier / speculative spend should stand down so paid work keeps working.
DEFAULT_FLOOR_USD = 50.0

_CACHE_TTL_SECONDS = 60.0
_cache: dict[str, tuple[float, float]] = {}  # api_key -> (fetched_at, balance_usd)


def get_balance_usd(api_key: str | None = None, *, timeout: float = 10.0,
                    use_cache: bool = True) -> float | None:
    """Live prepaid balance in USD, or None if it can't be determined.

    Cached briefly so a per-item guard doesn't hammer the endpoint. Never raises —
    callers decide what an unknown balance means (see `check_balance_floor`).
    """
    key = api_key or os.environ.get("SOCIALDATA_API_KEY", "")
    if not key:
        return None

    if use_cache:
        hit = _cache.get(key)
        if hit and (time.monotonic() - hit[0]) < _CACHE_TTL_SECONDS:
            return hit[1]

    try:
        import httpx

        resp = httpx.get(
            BALANCE_URL, headers={"Authorization": f"Bearer {key}"}, timeout=timeout
        )
        resp.raise_for_status()
        credits = float(resp.json()["balance"])
    except Exception:  # network, auth, shape — all mean "unknown", never fatal here
        log.warning("SocialData balance lookup failed", exc_info=True)
        return None

    usd = credits / _CREDITS_PER_USD
    _cache[key] = (time.monotonic(), usd)
    return usd


def check_balance_floor(
    floor_usd: float = DEFAULT_FLOOR_USD,
    *,
    api_key: str | None = None,
    on_unknown: Literal["block", "allow"] = "block",
) -> tuple[bool, str]:
    """(allowed, reason). Guard speculative SocialData spend behind a reserve.

    `on_unknown` is the load-bearing choice and has no safe default for every caller:

    - **"block"** (default, for FREE-TIER work): if we can't read the balance we don't
      spend. Skipping a prospect's audit is cheap; draining the balance and 402-ing a
      paying client's sweep is not.
    - **"allow"** (for PAID/client work): a transient lookup failure must not stop
      delivery someone is paying for.
    """
    balance = get_balance_usd(api_key)
    if balance is None:
        if on_unknown == "allow":
            return True, "balance_unknown_allowed"
        return False, "balance_unknown"
    if balance < floor_usd:
        return False, f"below_floor:{balance:.2f}<{floor_usd:.2f}"
    return True, f"ok:{balance:.2f}"
