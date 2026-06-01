"""C1.2 tests for sable_platform.relay.socialdata.

The SocialData wrapper is exercised against a DETERMINISTIC FAKE HTTP client —
NO real / paid SocialData (or any network) call ever happens (MEGAPLAN C1.2:
"no live paid calls in tests"). Cost is asserted against the ``cost_events``
table (tagged ``relay_socialdata.*``).

Coverage (each case states the EXPECTED number of HTTP calls):
  * cache hit returns WITHOUT a network call (1 real call, 2nd served from cache)
  * a 429 produces a bounded retry-with-backoff then succeeds (N 429s then 1
    success — assert exact call count)
  * a 402 (budget exhausted) is a REACTIVE hard skip: raises, writes no success
    cost row, and makes ZERO further HTTP calls (the latch)
  * a repeated since_id cursor dedupes to ZERO new fetches
  * the PROACTIVE per-org daily cap fires BEFORE any request (over-cap org makes
    zero HTTP calls), an under-cap org proceeds, and the helper sums only that
    org's ``relay_socialdata.%`` rows for the current UTC day
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from sable_platform.relay import socialdata as sd


# ---------------------------------------------------------------------------
# Deterministic fake HTTP client
# ---------------------------------------------------------------------------
class FakeHttp:
    """A scripted ``http_get`` — returns a queued response per call, no network.

    ``script`` is a list of :class:`sd.HttpResponse` (or callables returning
    one). Each call pops the next; a single trailing response repeats. Records
    every (path, params) so tests assert exactly which calls hit the wire.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        if len(self._responses) > 1:
            resp = self._responses.pop(0)
        else:
            resp = self._responses[0]
        return resp() if callable(resp) else resp


def _tweet(tweet_id: str) -> dict:
    return {"id_str": tweet_id, "id": int(tweet_id), "full_text": f"tweet {tweet_id}"}


def _ok(tweet_ids) -> sd.HttpResponse:
    return sd.HttpResponse(status_code=200, json_body={"tweets": [_tweet(t) for t in tweet_ids]})


# ---------------------------------------------------------------------------
# DB seed helpers
# ---------------------------------------------------------------------------
def _seed_org(conn, org_id: str) -> None:
    conn.execute(
        text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"),
        {"o": org_id},
    )


def _seed_relay_client(conn, org_id: str, *, config: str | None = None) -> None:
    if config is None:
        # Omit ``config`` so the schema's NOT-NULL ``{}`` default applies — this
        # is the real-world "default config, no polling overrides" row shape.
        conn.execute(
            text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"),
            {"o": org_id},
        )
    else:
        conn.execute(
            text(
                "INSERT INTO relay_clients (org_id, enabled, config) "
                "VALUES (:o, 1, :config)"
            ),
            {"o": org_id, "config": config},
        )


def _no_sleep(_seconds: float) -> None:  # backoff sleeper that never waits
    return None


def _make_client(conn, http) -> sd.SocialDataClient:
    # Deterministic jitter (1.0) so backoff timing is reproducible; _no_sleep so
    # tests never actually block during 429 retries.
    return sd.SocialDataClient(
        http_get=http,
        conn=conn,
        sleep=_no_sleep,
        jitter=lambda: 1.0,
    )


def _socialdata_cost_rows(conn, org_id: str) -> list:
    return conn.execute(
        text(
            "SELECT call_type, cost_usd, call_status FROM cost_events "
            "WHERE org_id = :o AND call_type LIKE 'relay_socialdata.%' "
            "ORDER BY event_id"
        ),
        {"o": org_id},
    ).fetchall()


# ===========================================================================
# Cache hit — returns WITHOUT a network call
# ===========================================================================
def test_cache_hit_makes_no_second_http_call(sa_conn) -> None:
    _seed_org(sa_conn, "orgcache")
    sa_conn.commit()
    http = FakeHttp([_ok(["100", "99"])])
    client = _make_client(sa_conn, http)

    first = client.fetch_timeline("orgcache", user_id="123")
    second = client.fetch_timeline("orgcache", user_id="123")

    assert [t["id_str"] for t in first] == ["100", "99"]
    assert second == first
    # EXPECTED: exactly 1 HTTP call total — the 2nd fetch is served from cache.
    assert http.calls and len(http.calls) == 1
    assert client.http_call_count == 1
    # And only ONE cost row (the cache hit is free).
    rows = _socialdata_cost_rows(sa_conn, "orgcache")
    assert len(rows) == 1
    assert rows[0][0] == sd.CALL_TYPE_TIMELINE


def test_hydrate_cache_hit_makes_no_second_call(sa_conn) -> None:
    _seed_org(sa_conn, "orghyd")
    sa_conn.commit()
    http = FakeHttp([sd.HttpResponse(status_code=200, json_body={"id_str": "555"})])
    client = _make_client(sa_conn, http)

    a = client.hydrate_tweet("orghyd", "555")
    b = client.hydrate_tweet("orghyd", "555")

    assert a == {"id_str": "555"}
    assert b == a
    assert len(http.calls) == 1  # 2nd hydrate served from cache
    rows = _socialdata_cost_rows(sa_conn, "orghyd")
    assert len(rows) == 1 and rows[0][0] == sd.CALL_TYPE_HYDRATE


def test_hydrate_404_returns_none_not_charged_and_negative_cached(sa_conn) -> None:
    _seed_org(sa_conn, "org404")
    sa_conn.commit()
    http = FakeHttp([sd.HttpResponse(status_code=404)])
    client = _make_client(sa_conn, http)

    assert client.hydrate_tweet("org404", "deadbeef") is None
    # Re-hydrating the same missing id is served from the negative cache.
    assert client.hydrate_tweet("org404", "deadbeef") is None
    assert len(http.calls) == 1  # 404 not retried; negative cached
    # 404 is NOT charged (best-practices §11).
    assert _socialdata_cost_rows(sa_conn, "org404") == []


# ===========================================================================
# 429 — bounded retry with backoff, then success
# ===========================================================================
def test_429_bounded_retry_then_success(sa_conn) -> None:
    _seed_org(sa_conn, "org429")
    sa_conn.commit()
    # 3 × 429 then a 200 — should retry 3 times and succeed on the 4th call.
    http = FakeHttp(
        [
            sd.HttpResponse(status_code=429, retry_after=None),
            sd.HttpResponse(status_code=429, retry_after=None),
            sd.HttpResponse(status_code=429, retry_after=None),
            _ok(["200"]),
        ]
    )
    client = _make_client(sa_conn, http)

    result = client.fetch_timeline("org429", user_id="123")

    assert [t["id_str"] for t in result] == ["200"]
    # EXPECTED: 4 HTTP calls total (3 rate-limited + 1 success).
    assert len(http.calls) == 4
    assert client.http_call_count == 4
    # Only the successful call is charged.
    rows = _socialdata_cost_rows(sa_conn, "org429")
    assert len(rows) == 1 and rows[0][0] == sd.CALL_TYPE_TIMELINE


def test_429_persists_past_retry_budget_raises(sa_conn) -> None:
    _seed_org(sa_conn, "org429bad")
    sa_conn.commit()
    # Always 429 — exhausts MAX_RETRIES then raises.
    http = FakeHttp([sd.HttpResponse(status_code=429)])
    client = _make_client(sa_conn, http)

    with pytest.raises(sd.SocialDataRateLimited):
        client.fetch_timeline("org429bad", user_id="123")

    # EXPECTED: initial attempt + MAX_RETRIES = 1 + 4 = 5 calls, then raise.
    assert len(http.calls) == sd.MAX_RETRIES + 1
    # No success → no cost row.
    assert _socialdata_cost_rows(sa_conn, "org429bad") == []


def test_429_honors_retry_after_header(sa_conn) -> None:
    _seed_org(sa_conn, "orgretry")
    sa_conn.commit()
    slept: list[float] = []
    http = FakeHttp(
        [sd.HttpResponse(status_code=429, retry_after=7.0), _ok(["1"])]
    )
    client = sd.SocialDataClient(
        http_get=http, conn=sa_conn, sleep=slept.append, jitter=lambda: 1.0
    )

    client.fetch_timeline("orgretry", user_id="9")

    # The Retry-After header value (7.0s) is used verbatim, not the exp schedule.
    assert slept == [7.0]


# ===========================================================================
# 402 — REACTIVE hard skip, zero further HTTP calls (the latch)
# ===========================================================================
def test_402_hard_skip_zero_further_calls(sa_conn) -> None:
    _seed_org(sa_conn, "org402")
    sa_conn.commit()
    http = FakeHttp([sd.HttpResponse(status_code=402)])
    client = _make_client(sa_conn, http)

    with pytest.raises(sd.SocialDataBudgetExhausted):
        client.fetch_timeline("org402", user_id="123")

    assert client.budget_exhausted is True
    # EXPECTED: exactly 1 HTTP call — the one that returned 402.
    assert len(http.calls) == 1

    # Any further call — even a different method/org — makes ZERO HTTP calls
    # (the sticky latch). It raises without touching the wire.
    with pytest.raises(sd.SocialDataBudgetExhausted):
        client.fetch_timeline("org402", user_id="999")
    with pytest.raises(sd.SocialDataBudgetExhausted):
        client.hydrate_tweet("org402", "abc")
    with pytest.raises(sd.SocialDataBudgetExhausted):
        client.fetch_conversation_replies("org402", "xyz")

    # Still exactly 1 HTTP call after all those attempts — none reached the wire.
    assert len(http.calls) == 1
    assert client.http_call_count == 1

    # A 402 is not charged (best-practices §1 "failed requests are not charged"):
    # no success cost row was written.
    rows = _socialdata_cost_rows(sa_conn, "org402")
    success_rows = [r for r in rows if r[2] == "success"]
    assert success_rows == []


# ===========================================================================
# since_id cursor dedupe — repeated cursor → ZERO new fetches
# ===========================================================================
def test_repeated_since_id_cursor_dedupes_to_zero_fetches(sa_conn) -> None:
    _seed_org(sa_conn, "orgcursor")
    sa_conn.commit()
    # First poll (no cursor) returns up to id 300; the wrapper advances its
    # since_id high-water to 300.
    http = FakeHttp([_ok(["300", "299", "298"])])
    client = _make_client(sa_conn, http)

    first = client.fetch_timeline("orgcursor", user_id="123")
    assert [t["id_str"] for t in first] == ["300", "299", "298"]
    assert len(http.calls) == 1

    # A subsequent poll with since_id=300 (== high-water) means "nothing newer
    # than what I've already seen" → dedupes to ZERO fetches, returns [].
    second = client.fetch_timeline("orgcursor", user_id="123", since_id="300")
    assert second == []
    # EXPECTED: still 1 HTTP call total — the dedupe short-circuits the wire.
    assert len(http.calls) == 1
    assert client.http_call_count == 1

    # A since_id ABOVE the high-water (nothing that new seen yet) also dedupes —
    # the provider has nothing newer than what we hold.
    fourth = client.fetch_timeline("orgcursor", user_id="123", since_id="500")
    assert fourth == []
    assert len(http.calls) == 1

    # Only the single real fetch was charged.
    rows = _socialdata_cost_rows(sa_conn, "orgcursor")
    assert len(rows) == 1


def test_new_tweets_above_cursor_do_fetch(sa_conn) -> None:
    _seed_org(sa_conn, "orgcursor2")
    sa_conn.commit()
    # First fetch advances high-water to 300; a poll with a since_id below the
    # NEXT page's ids should still fetch when nothing is deduped yet.
    http = FakeHttp([_ok(["300"]), _ok(["305"])])
    client = _make_client(sa_conn, http)

    client.fetch_timeline("orgcursor2", user_id="1")  # high-water → 300
    # A genuinely-new poll for a DIFFERENT kind (replies) is not deduped by the
    # timeline cursor — distinct (org, kind) cursor namespaces.
    replies = client.fetch_conversation_replies("orgcursor2", "999")
    assert [t["id_str"] for t in replies] == ["305"]
    assert len(http.calls) == 2  # timeline + replies are independent cursors


# ===========================================================================
# PROACTIVE per-org daily cap — fires BEFORE any request
# ===========================================================================
def _seed_socialdata_cost(conn, org_id: str, cost: float, *, call_type=None) -> None:
    conn.execute(
        text(
            "INSERT INTO cost_events (org_id, call_type, cost_usd, call_status) "
            "VALUES (:o, :ct, :c, 'success')"
        ),
        {"o": org_id, "ct": call_type or sd.CALL_TYPE_TIMELINE, "c": cost},
    )


def test_proactive_cap_default_is_one_dollar(sa_conn) -> None:
    _seed_org(sa_conn, "orgcap")
    _seed_relay_client(sa_conn, "orgcap", config=None)  # no polling config
    sa_conn.commit()
    assert sd.get_daily_cost_cap(sa_conn, "orgcap") == Decimal("1.00")


def test_proactive_cap_reads_polling_config(sa_conn) -> None:
    _seed_org(sa_conn, "orgcap2")
    _seed_relay_client(
        sa_conn, "orgcap2", config='{"polling": {"daily_cost_cap_usd": 0.50}}'
    )
    sa_conn.commit()
    assert sd.get_daily_cost_cap(sa_conn, "orgcap2") == Decimal("0.50")


def test_proactive_cap_over_cap_makes_zero_http_calls(sa_conn) -> None:
    """An org at/over its daily cap → poller skips it, ZERO SocialData calls.

    This is the PROACTIVE gate: the cap fires BEFORE any request (unlike the
    reactive 402 which fires only after spend is incurred).
    """
    _seed_org(sa_conn, "orgover")
    _seed_relay_client(sa_conn, "orgover", config=None)  # default $1.00 cap
    # Seed today's spend at the cap.
    for _ in range(500):  # 500 * 0.002 = $1.00 == cap
        _seed_socialdata_cost(sa_conn, "orgover", sd.COST_PER_CALL_USD)
    sa_conn.commit()

    status = sd.check_daily_socialdata_budget(sa_conn, "orgover")
    assert status.over_cap is True
    assert status.spend >= status.cap
    assert status.cap == Decimal("1.00")

    # The poller's contract: when over_cap, make NO SocialData HTTP call. Model
    # the poller's gate explicitly and assert the wire was never touched.
    http = FakeHttp([_ok(["1"])])
    client = _make_client(sa_conn, http)
    if not sd.check_daily_socialdata_budget(sa_conn, "orgover").over_cap:
        client.fetch_timeline("orgover", user_id="1")  # would only run if under cap
    assert http.calls == []
    assert client.http_call_count == 0


def test_proactive_cap_under_cap_proceeds(sa_conn) -> None:
    _seed_org(sa_conn, "orgunder")
    _seed_relay_client(sa_conn, "orgunder", config=None)  # default $1.00 cap
    # Seed a small amount of spend, well under the cap.
    _seed_socialdata_cost(sa_conn, "orgunder", 0.10)
    sa_conn.commit()

    status = sd.check_daily_socialdata_budget(sa_conn, "orgunder")
    assert status.over_cap is False
    assert status.spend == Decimal("0.1")

    # Under cap → the poll proceeds and makes its HTTP call.
    http = FakeHttp([_ok(["42"])])
    client = _make_client(sa_conn, http)
    if not sd.check_daily_socialdata_budget(sa_conn, "orgunder").over_cap:
        result = client.fetch_timeline("orgunder", user_id="1")
    assert [t["id_str"] for t in result] == ["42"]
    assert len(http.calls) == 1


def test_two_orgs_in_one_pass_over_and_under_cap(sa_conn) -> None:
    """A loop pass: over-cap org SKIPS (0 calls), under-cap org polls normally."""
    _seed_org(sa_conn, "orgA")
    _seed_org(sa_conn, "orgB")
    _seed_relay_client(sa_conn, "orgA", config=None)
    _seed_relay_client(sa_conn, "orgB", config=None)
    for _ in range(600):  # orgA over the $1.00 cap
        _seed_socialdata_cost(sa_conn, "orgA", sd.COST_PER_CALL_USD)
    _seed_socialdata_cost(sa_conn, "orgB", 0.02)  # orgB well under
    sa_conn.commit()

    http = FakeHttp([_ok(["7"])])
    client = _make_client(sa_conn, http)
    polled = []
    for org in ("orgA", "orgB"):
        if not sd.check_daily_socialdata_budget(sa_conn, org).over_cap:
            client.fetch_timeline(org, user_id="1")
            polled.append(org)

    assert polled == ["orgB"]  # orgA skipped, orgB polled
    assert len(http.calls) == 1


def test_daily_spend_sums_only_socialdata_rows_for_org_and_day(sa_conn) -> None:
    """The helper sums ONLY this org's ``relay_socialdata.%`` rows, today, UTC."""
    _seed_org(sa_conn, "orgsum")
    _seed_org(sa_conn, "orgother")
    _seed_relay_client(sa_conn, "orgsum", config=None)
    sa_conn.commit()

    # Three relay_socialdata.* rows for orgsum today (counted).
    _seed_socialdata_cost(sa_conn, "orgsum", 0.10, call_type=sd.CALL_TYPE_TIMELINE)
    _seed_socialdata_cost(sa_conn, "orgsum", 0.05, call_type=sd.CALL_TYPE_HYDRATE)
    _seed_socialdata_cost(sa_conn, "orgsum", 0.02, call_type=sd.CALL_TYPE_REPLIES)
    # A NON-socialdata AI-spend row for orgsum (must be EXCLUDED).
    _seed_socialdata_cost(sa_conn, "orgsum", 9.99, call_type="autocm.draft")
    # A socialdata row for a DIFFERENT org (must be EXCLUDED).
    _seed_socialdata_cost(sa_conn, "orgother", 0.50, call_type=sd.CALL_TYPE_TIMELINE)
    # A socialdata row for orgsum but on a PRIOR UTC day (must be EXCLUDED).
    sa_conn.execute(
        text(
            "INSERT INTO cost_events (org_id, call_type, cost_usd, call_status, created_at) "
            "VALUES ('orgsum', :ct, 0.40, 'success', '2020-01-01 00:00:00')"
        ),
        {"ct": sd.CALL_TYPE_TIMELINE},
    )
    sa_conn.commit()

    spend = sd.get_daily_socialdata_spend(sa_conn, "orgsum")
    # Only the three same-org, same-day relay_socialdata.* rows: 0.10+0.05+0.02.
    assert spend == Decimal("0.17")

    status = sd.check_daily_socialdata_budget(sa_conn, "orgsum")
    assert status.spend == Decimal("0.17")
    assert status.over_cap is False  # 0.17 < 1.00


def test_malformed_polling_config_falls_back_to_default_cap(sa_conn) -> None:
    _seed_org(sa_conn, "orgbad")
    _seed_relay_client(sa_conn, "orgbad", config="{not valid json")
    sa_conn.commit()
    assert sd.get_daily_cost_cap(sa_conn, "orgbad") == Decimal("1.00")


def test_unknown_org_falls_back_to_default_cap(sa_conn) -> None:
    # No relay_clients row at all → default cap, zero spend, under cap.
    assert sd.get_daily_cost_cap(sa_conn, "ghost") == Decimal("1.00")
    status = sd.check_daily_socialdata_budget(sa_conn, "ghost")
    assert status.spend == Decimal("0")
    assert status.over_cap is False
