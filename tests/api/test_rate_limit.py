"""Tests for the in-memory rate limiter."""
from __future__ import annotations

from sable_platform.api.rate_limit import RateLimitConfig, RateLimiter


def test_under_cap_allowed():
    rl = RateLimiter(RateLimitConfig(read_per_min_token=3, per_min_ip=10))
    for _ in range(3):
        allowed, _ = rl.check(token_id="t", ip="1.2.3.4", scope_class="read")
        assert allowed


def test_per_token_cap_triggers_429():
    rl = RateLimiter(RateLimitConfig(read_per_min_token=2, per_min_ip=100))
    rl.check(token_id="t", ip="ip", scope_class="read", now=100.0)
    rl.check(token_id="t", ip="ip", scope_class="read", now=100.1)
    allowed, retry = rl.check(token_id="t", ip="ip", scope_class="read", now=100.2)
    assert not allowed
    assert retry >= 1


def test_per_ip_cap_triggers_429():
    rl = RateLimiter(RateLimitConfig(read_per_min_token=999, per_min_ip=2))
    rl.check(token_id="a", ip="1.1.1.1", scope_class="read", now=10.0)
    rl.check(token_id="b", ip="1.1.1.1", scope_class="read", now=10.1)
    allowed, _ = rl.check(token_id="c", ip="1.1.1.1", scope_class="read", now=10.2)
    assert not allowed


def test_window_advances_after_60s():
    rl = RateLimiter(RateLimitConfig(read_per_min_token=1, per_min_ip=10))
    rl.check(token_id="t", ip="x", scope_class="read", now=0.0)
    # Just before the window: still rate-limited.
    allowed, _ = rl.check(token_id="t", ip="x", scope_class="read", now=59.0)
    assert not allowed
    # After the window: allowed again.
    allowed, _ = rl.check(token_id="t", ip="x", scope_class="read", now=61.0)
    assert allowed


def test_read_and_write_buckets_are_independent():
    rl = RateLimiter(RateLimitConfig(
        read_per_min_token=1, write_per_min_token=1, per_min_ip=100,
    ))
    # First read and first write are both under their own caps -> allowed.
    a_read, _ = rl.check(token_id="t", ip="x", scope_class="read", now=0.0)
    a_write, _ = rl.check(token_id="t", ip="x", scope_class="write", now=0.1)
    assert a_read
    assert a_write
    # Second read and second write should each be blocked by their own bucket.
    blocked_read, _ = rl.check(token_id="t", ip="x", scope_class="read", now=0.2)
    blocked_write, _ = rl.check(token_id="t", ip="x", scope_class="write", now=0.3)
    assert not blocked_read
    assert not blocked_write
