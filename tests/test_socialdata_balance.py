"""The prepaid-balance guard.

SocialData is shared by the relay sweep, Cult Grader, SableKOL and the community audit.
Hitting $0 returns HTTP 402 and breaks all of them — it happened on 2026-05-07. The
floor exists so free-tier work fails before paid delivery does.
"""
from __future__ import annotations

import pytest

from sable_platform import socialdata_balance as sb


@pytest.fixture(autouse=True)
def _clear_cache():
    sb._cache.clear()
    yield
    sb._cache.clear()


def _stub(monkeypatch, balance):
    """Stub get_balance_usd; None models an unreadable balance."""
    monkeypatch.setattr(sb, "get_balance_usd", lambda *a, **k: balance)


def test_allows_when_comfortably_above_floor(monkeypatch):
    _stub(monkeypatch, 109.80)
    ok, reason = sb.check_balance_floor(50.0)
    assert ok and reason.startswith("ok:")


def test_blocks_below_floor(monkeypatch):
    _stub(monkeypatch, 42.0)
    ok, reason = sb.check_balance_floor(50.0)
    assert not ok
    assert "below_floor" in reason


def test_exactly_at_floor_is_allowed(monkeypatch):
    _stub(monkeypatch, 50.0)
    ok, _ = sb.check_balance_floor(50.0)
    assert ok, "the floor is a reserve, not a tripwire one cent above it"


def test_unknown_balance_BLOCKS_free_tier_work_by_default(monkeypatch):
    """Fail CLOSED for speculative spend: skipping a prospect's audit is cheap,
    402-ing a paying client's sweep is not."""
    _stub(monkeypatch, None)
    ok, reason = sb.check_balance_floor(50.0)
    assert not ok
    assert reason == "balance_unknown"


def test_unknown_balance_ALLOWS_when_caller_opts_in(monkeypatch):
    """Paid delivery must not stop on a transient lookup blip."""
    _stub(monkeypatch, None)
    ok, reason = sb.check_balance_floor(50.0, on_unknown="allow")
    assert ok
    assert reason == "balance_unknown_allowed"


def test_missing_api_key_yields_unknown_not_a_crash(monkeypatch):
    monkeypatch.delenv("SOCIALDATA_API_KEY", raising=False)
    assert sb.get_balance_usd(api_key=None) is None


def test_network_failure_is_unknown_never_raises(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "get", boom)
    assert sb.get_balance_usd(api_key="k", use_cache=False) is None


def test_credits_are_converted_to_dollars(monkeypatch):
    """1 credit = $0.01. Getting this wrong by 100x would silently disable the floor."""
    import httpx

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"balance": 10980.16}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    assert sb.get_balance_usd(api_key="k", use_cache=False) == pytest.approx(109.8016)
