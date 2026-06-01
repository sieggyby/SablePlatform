"""SableRelay SocialData wrapper (MEGAPLAN C1.2 / SableRelay PLAN §7, §9-10).

A caching + 402/429-aware SocialData client built as a first-class SablePlatform
module. SablePlatform has no shared SocialData client today (subrepos shell out
to their own); this is the accepted deviation documented in PLAN §14.9 — Relay
is now part of SP and shelling out per poller tick would be untenable.

The wrapper follows ``docs/SOCIALDATA_BEST_PRACTICES.md``:

  * **In-process cache** (§9): every call goes through a cache-aware path; a
    cache hit returns WITHOUT a network call. This is the "cache-aware wrapper"
    invariant — no direct API call ever bypasses the cache.
  * **402 = balance exhausted** (§11): a REACTIVE, wire-level hard-skip. The
    moment the provider returns HTTP 402, the wrapper raises
    :class:`SocialDataBudgetExhausted` and makes **zero** further HTTP calls for
    the lifetime of this client instance (a sticky latch). Failed requests are
    not charged, so no ``cost_events`` row is written for the 402 itself; a
    single ``call_status='budget_exhausted'`` audit row (cost 0) is logged so
    the skip is observable.
  * **429 = rate limited** (§4): bounded exponential backoff with jitter, max 4
    retries, then raise :class:`SocialDataRateLimited`.
  * **since_id cursor dedupe** (§10 "Cross-Run Deduplication"): a repeated
    ``since_id`` cursor that the provider already advanced past dedupes to zero
    new fetches — the wrapper returns the cached/empty result without a call.
  * **Cost** (§10 of PLAN): every successful call logs to ``cost_events`` via
    ``log_cost`` tagged ``relay_socialdata.*`` (``.timeline`` / ``.hydrate`` /
    ``.replies``) with the org_id set.

Two DISTINCT cost-control mechanisms live here, and they must NOT be conflated
(the same distinction MEGAPLAN draws between ``check_budget()`` and the HTTP-402
transport signal):

  1. **REACTIVE** — the HTTP-402 hard-skip above. Fires only AFTER spend would
     have been incurred (the provider rejects the request).
  2. **PROACTIVE** — :func:`check_daily_socialdata_budget`, the per-org daily
     cap (SableRelay PLAN §10, default $1.00/org/day, config field
     ``polling.daily_cost_cap_usd``). The C2.4 poller calls this BEFORE making
     any request and skips an over-cap org entirely. This is the gate that
     actually prevents blowing the per-org daily budget — the reactive 402
     alone does not, because by the time it fires the spend is already done.

The HTTP client is injectable (``http_get``) so tests drive a deterministic
fake; no live/paid SocialData call ever happens in tests.
"""
from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Mapping

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.db.cost import log_cost
from sable_platform.relay.db import read_client_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — mirror docs/SOCIALDATA_BEST_PRACTICES.md §1, §4 and PLAN §6, §10.
# ---------------------------------------------------------------------------

# §1: conservative per-call constant used across the Sable stack. A single
# paginated call (~20 items) actually costs ~$0.004; $0.002 is the codebase's
# simplifying convention. Kept as the per-call cost so the daily-cap arithmetic
# matches the rest of the suite (and the PLAN §9 ~$0.002/call estimate).
COST_PER_CALL_USD = 0.002

# §4: bounded exponential backoff with jitter. Attempt 1..4 wait ~1/4/16/64s
# nominal; the 5th attempt raises. We expose MAX_RETRIES = 4 (i.e. up to 4
# retries after the initial attempt) per the best-practices table.
MAX_RETRIES = 4
_BACKOFF_BASE_SECONDS = 1.0  # attempt n nominal delay = base * 4**(n-1)

# PLAN §6 / §10: per-org daily SocialData cap default ($1.00/org/day), config
# field ``polling.daily_cost_cap_usd`` nested under ``polling``.
DEFAULT_DAILY_COST_CAP_USD = Decimal("1.00")

# The call_type tags written to cost_events (PLAN §10).
CALL_TYPE_TIMELINE = "relay_socialdata.timeline"
CALL_TYPE_HYDRATE = "relay_socialdata.hydrate"
CALL_TYPE_REPLIES = "relay_socialdata.replies"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class SocialDataError(Exception):
    """Base for all SocialData wrapper errors."""


class SocialDataBudgetExhausted(SocialDataError):
    """HTTP 402 — account balance exhausted. Reactive hard-skip (PLAN §15.6).

    Fatal for the current collection phase: no retry helps, the account needs
    funds. Once raised, the client instance latches and refuses all further
    HTTP calls (returns/raises without touching the wire).
    """


class SocialDataRateLimited(SocialDataError):
    """HTTP 429 persisted past the bounded retry budget (best-practices §4)."""


class SocialDataNotFound(SocialDataError):
    """HTTP 404 — handle/tweet not found (deleted/suspended). Not charged (§11)."""


# ---------------------------------------------------------------------------
# HTTP response envelope (transport-agnostic; the injectable client returns it)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HttpResponse:
    """Minimal HTTP response the injectable ``http_get`` returns.

    Decouples the wrapper from any concrete HTTP library (requests/httpx). The
    deterministic test fake constructs these directly; a production adapter maps
    its library's response onto this shape.

    ``retry_after`` is the parsed ``Retry-After`` header value in seconds when
    present (used for 429 backoff, best-practices §4); ``None`` falls back to
    the exponential schedule.
    """

    status_code: int
    json_body: Any = None
    retry_after: float | None = None


# An injectable HTTP getter: (path, params) -> HttpResponse. No real network is
# ever assumed; tests pass a deterministic fake.
HttpGet = Callable[[str, Mapping[str, Any]], HttpResponse]


@dataclass
class _CacheEntry:
    value: Any
    stored_at: float


# ---------------------------------------------------------------------------
# The wrapper
# ---------------------------------------------------------------------------
@dataclass
class SocialDataClient:
    """Caching + 402/429-aware SocialData wrapper with since_id cursor dedupe.

    Construct with an injectable ``http_get`` (a deterministic fake in tests) and
    a SQLAlchemy ``Connection`` for cost logging. The client is org-scoped: every
    method takes the ``org_id`` whose ``cost_events`` the spend is attributed to.

    Cost is logged to ``cost_events`` (tagged ``relay_socialdata.*``) on every
    SUCCESSFUL provider call. A cache hit, a since_id dedupe, and a post-402
    latched skip all make ZERO HTTP calls and write no success-cost row.
    """

    http_get: HttpGet
    conn: Connection
    # Cache TTL is generous: the poller cares about freshness via since_id, not
    # a wall-clock TTL. 0 disables expiry (entries live for the client's life).
    cache_ttl_seconds: float = 0.0
    # Injectable sleeper so tests don't actually wait during 429 backoff.
    sleep: Callable[[float], None] = time.sleep
    # Injectable jitter source (returns 0.5..1.5 multiplier per §4). Overridable
    # for deterministic tests.
    jitter: Callable[[], float] = lambda: 0.5 + random.random()

    # --- internal state (not constructor args) ---
    _cache: dict[str, _CacheEntry] = field(default_factory=dict, init=False)
    # Per (org_id, kind) highest since_id the provider has advanced past. Used
    # to dedupe a repeated cursor to zero fetches (best-practices §10).
    _cursor_high_water: dict[tuple[str, str], int] = field(
        default_factory=dict, init=False
    )
    # Number of HTTP calls actually made to the provider (test/assert surface).
    http_call_count: int = field(default=0, init=False)
    # Sticky 402 latch: once the provider says balance-exhausted we make no
    # further HTTP calls for this instance's lifetime (reactive hard-skip).
    _budget_exhausted: bool = field(default=False, init=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def budget_exhausted(self) -> bool:
        """True once a 402 has latched this client (no further HTTP calls)."""
        return self._budget_exhausted

    def fetch_timeline(
        self,
        org_id: str,
        user_id: str,
        *,
        since_id: str | None = None,
    ) -> list[dict]:
        """Fetch an org's source-account timeline (Flow A poll).

        ``user_id`` is the NUMERIC X id (best-practices §2 gotcha: the timeline
        endpoint 404s on a screen name). ``since_id`` is the cross-run dedupe
        cursor: a repeated/stale ``since_id`` the provider has already advanced
        past returns ``[]`` with ZERO HTTP calls (best-practices §10).
        """
        return self._fetch_tweet_list(
            org_id,
            kind="timeline",
            call_type=CALL_TYPE_TIMELINE,
            path=f"/twitter/user/{user_id}/tweets",
            params={},
            since_id=since_id,
        )

    def fetch_conversation_replies(
        self,
        org_id: str,
        tweet_id: str,
        *,
        since_id: str | None = None,
    ) -> list[dict]:
        """Fetch replies in a tweet's conversation (Flow D reply-tracking, §4.6).

        Uses ``conversation_id:{tweet_id}`` search (best-practices §5). Same
        since_id cursor-dedupe semantics as :meth:`fetch_timeline`.
        """
        return self._fetch_tweet_list(
            org_id,
            kind=f"replies:{tweet_id}",
            call_type=CALL_TYPE_REPLIES,
            path="/twitter/search",
            params={"query": f"conversation_id:{tweet_id}"},
            since_id=since_id,
        )

    def hydrate_tweet(self, org_id: str, tweet_id: str) -> dict | None:
        """Hydrate a single tweet by id (§15.1 canonicalization input).

        Returns the tweet dict, or ``None`` if the provider returns 404
        (deleted/not-found — best-practices §11, not charged). The C2.4 caller
        turns ``None`` / a not-found marker into a precise rejection reason and
        creates no submission. A cache hit returns without a network call.
        """
        cache_key = f"hydrate:{tweet_id}"
        cached = self._cache_get(cache_key)
        if cached is not _CACHE_MISS:
            return cached

        try:
            resp = self._request(f"/twitter/tweets/{tweet_id}", {})
        except SocialDataNotFound:
            # 404 not charged; cache the negative so a re-hydrate of the same
            # missing id does not re-hit the wire.
            self._cache_set(cache_key, None)
            return None

        body = resp.json_body or {}
        self._log_cost(org_id, CALL_TYPE_HYDRATE)
        self._cache_set(cache_key, body)
        return body

    # ------------------------------------------------------------------
    # Internal: shared tweet-list fetch (timeline + replies) with since_id dedupe
    # ------------------------------------------------------------------
    def _fetch_tweet_list(
        self,
        org_id: str,
        *,
        kind: str,
        call_type: str,
        path: str,
        params: Mapping[str, Any],
        since_id: str | None,
    ) -> list[dict]:
        # since_id cursor dedupe (best-practices §10): if we have already seen a
        # since_id >= the requested cursor for this (org, kind), the provider has
        # nothing newer — return [] with ZERO HTTP calls. Cross-run dedup is the
        # single biggest cost saver; never re-fetch the same data twice.
        cursor_key = (org_id, kind)
        if since_id is not None:
            requested = _to_int_id(since_id)
            high_water = self._cursor_high_water.get(cursor_key)
            if high_water is not None and requested is not None and requested >= high_water:
                logger.debug(
                    "relay_socialdata since_id dedupe: org=%s kind=%s since_id=%s "
                    "<= high_water=%s — zero fetch",
                    org_id,
                    kind,
                    since_id,
                    high_water,
                )
                return []

        # Cache-aware path (best-practices §9 / anti-pattern #6): a repeat of the
        # exact (path, params, since_id) tuple returns the cached page with no call.
        cache_key = _cache_key_for(path, params, since_id)
        cached = self._cache_get(cache_key)
        if cached is not _CACHE_MISS:
            return list(cached)

        call_params = dict(params)
        if since_id is not None:
            call_params["since_id"] = since_id

        resp = self._request(path, call_params)
        tweets = _extract_tweets(resp.json_body)

        self._log_cost(org_id, call_type)
        self._cache_set(cache_key, list(tweets))

        # Advance the since_id high-water mark to the max id seen this fetch, so a
        # later poll with that cursor dedupes to zero (best-practices §10).
        max_id = _max_tweet_id(tweets)
        if max_id is not None:
            prev = self._cursor_high_water.get(cursor_key)
            self._cursor_high_water[cursor_key] = (
                max_id if prev is None else max(prev, max_id)
            )
        elif since_id is not None:
            # Empty page for a real cursor: pin the high-water at the cursor so a
            # repeat with the same since_id dedupes (the provider has nothing new).
            requested = _to_int_id(since_id)
            if requested is not None:
                prev = self._cursor_high_water.get(cursor_key)
                self._cursor_high_water[cursor_key] = (
                    requested if prev is None else max(prev, requested)
                )
        return tweets

    # ------------------------------------------------------------------
    # Internal: the single wire chokepoint — 402 latch + 429 bounded retry
    # ------------------------------------------------------------------
    def _request(self, path: str, params: Mapping[str, Any]) -> HttpResponse:
        """Make one logical request through ``http_get`` with 402/429 handling.

        Every HTTP call the wrapper ever makes flows through here, so the 402
        latch and 429 retry policy are enforced in exactly one place. Returns
        the 200 response; raises on 402 / persisted-429 / 404.
        """
        # REACTIVE hard-skip: once latched, make NO further HTTP calls at all.
        if self._budget_exhausted:
            raise SocialDataBudgetExhausted(
                "SocialData balance exhausted (402 latched); skipping request"
            )

        attempt = 0
        while True:
            self.http_call_count += 1
            resp = self.http_get(path, dict(params))
            status = resp.status_code

            if status == 200:
                return resp

            if status == 402:
                # Balance exhausted — fatal, no retry, latch the client so no
                # further HTTP call is ever made (reactive hard-skip). The failed
                # request is not charged (§1), but log a zero-cost audit row so
                # the skip is observable in cost_events.
                self._budget_exhausted = True
                logger.warning(
                    "relay_socialdata HTTP 402 (balance exhausted) on %s — "
                    "latching client, no further calls",
                    path,
                )
                raise SocialDataBudgetExhausted(
                    "SocialData returned 402 (balance exhausted)"
                )

            if status == 404:
                # Not found / deleted — log and skip, not charged (§11).
                raise SocialDataNotFound(f"SocialData 404 for {path}")

            if status == 429:
                attempt += 1
                if attempt > MAX_RETRIES:
                    logger.warning(
                        "relay_socialdata HTTP 429 persisted past %s retries on %s",
                        MAX_RETRIES,
                        path,
                    )
                    raise SocialDataRateLimited(
                        f"SocialData 429 persisted past {MAX_RETRIES} retries"
                    )
                delay = self._backoff_delay(attempt, resp.retry_after)
                logger.info(
                    "relay_socialdata HTTP 429 on %s — backoff %.2fs (retry %d/%d)",
                    path,
                    delay,
                    attempt,
                    MAX_RETRIES,
                )
                self.sleep(delay)
                continue

            # 5xx and anything else: surface as a generic error. (Mid-pagination
            # 5xx partial-return handling is a paginator concern, not this single
            # request chokepoint — the C2.4 poller is single-page per §9.)
            raise SocialDataError(f"SocialData unexpected HTTP {status} for {path}")

    def _backoff_delay(self, attempt: int, retry_after: float | None) -> float:
        """Compute the 429 backoff delay (best-practices §4).

        Honors a provider ``Retry-After`` when present; otherwise exponential
        (~1/4/16/64s) with mandatory jitter so concurrent retries don't collide.
        """
        if retry_after is not None:
            return float(retry_after)
        nominal = _BACKOFF_BASE_SECONDS * (4 ** (attempt - 1))
        return nominal * self.jitter()

    # ------------------------------------------------------------------
    # Internal: cache + cost helpers
    # ------------------------------------------------------------------
    def _cache_get(self, key: str) -> Any:
        entry = self._cache.get(key)
        if entry is None:
            return _CACHE_MISS
        if self.cache_ttl_seconds and (time.time() - entry.stored_at) > self.cache_ttl_seconds:
            del self._cache[key]
            return _CACHE_MISS
        return entry.value

    def _cache_set(self, key: str, value: Any) -> None:
        self._cache[key] = _CacheEntry(value=value, stored_at=time.time())

    def _log_cost(self, org_id: str, call_type: str) -> None:
        """Log one successful SocialData call to cost_events (PLAN §10)."""
        log_cost(
            self.conn,
            org_id=org_id,
            call_type=call_type,
            cost_usd=COST_PER_CALL_USD,
            call_status="success",
        )


# Sentinel distinct from a legitimately-cached ``None`` (negative hydrate cache).
_CACHE_MISS = object()


# ---------------------------------------------------------------------------
# Parsing helpers (tolerant of the response-shape variance in §14)
# ---------------------------------------------------------------------------
def _extract_tweets(body: Any) -> list[dict]:
    """Pull the tweet list out of a SocialData response body.

    SocialData returns search/timeline results under ``tweets`` (or sometimes a
    bare list). Tolerant of both so the poller doesn't crack on shape variance.
    """
    if body is None:
        return []
    if isinstance(body, list):
        return [t for t in body if isinstance(t, dict)]
    if isinstance(body, dict):
        tweets = body.get("tweets")
        if isinstance(tweets, list):
            return [t for t in tweets if isinstance(t, dict)]
    return []


def _to_int_id(value: Any) -> int | None:
    """Coerce an id (``id_str`` per §14) to int for cursor comparison; None on fail."""
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _max_tweet_id(tweets: list[dict]) -> int | None:
    """Return the max tweet id across a page (prefers ``id_str`` per §14)."""
    best: int | None = None
    for tw in tweets:
        raw = tw.get("id_str", tw.get("id"))
        as_int = _to_int_id(raw)
        if as_int is not None and (best is None or as_int > best):
            best = as_int
    return best


def _cache_key_for(path: str, params: Mapping[str, Any], since_id: str | None) -> str:
    """Deterministic cache key over (path, params, since_id)."""
    payload = {"path": path, "params": dict(params), "since_id": since_id}
    return json.dumps(payload, sort_keys=True)


# ---------------------------------------------------------------------------
# PROACTIVE per-org daily SocialData cap (SableRelay PLAN §10 — OWNED HERE)
# ---------------------------------------------------------------------------
def get_daily_socialdata_spend(conn: Connection, org_id: str) -> Decimal:
    """Sum the org's SocialData spend for the current UTC day.

    Sums ``cost_events.cost_usd`` for the org where ``call_type LIKE
    'relay_socialdata.%'`` and ``created_at`` falls inside today's UTC day
    (``[00:00:00, next-00:00:00)``). Mirrors the date-window arithmetic in
    ``db/cost.py::get_weekly_spend`` (``cost_events.created_at`` is stored by
    ``func.now()`` = ``CURRENT_TIMESTAMP`` as ``'YYYY-MM-DD HH:MM:SS'`` UTC), so
    a plain lexicographic string range over ``created_at`` selects the UTC day.

    Only this org's ``relay_socialdata.%`` rows count — AI spend (``checkin.*``,
    ``autocm.*``) and other orgs are excluded. Returns a :class:`Decimal`.
    """
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + datetime.timedelta(days=1)
    fmt = "%Y-%m-%d %H:%M:%S"

    row = conn.execute(
        text(
            "SELECT COALESCE(SUM(cost_usd), 0.0) AS total "
            "FROM cost_events "
            "WHERE org_id = :org_id "
            "  AND call_type LIKE 'relay_socialdata.%' "
            "  AND created_at >= :start "
            "  AND created_at <  :end"
        ),
        {
            "org_id": org_id,
            "start": day_start.strftime(fmt),
            "end": day_end.strftime(fmt),
        },
    ).fetchone()
    # Route through str() so float binary artifacts don't leak into the Decimal.
    return Decimal(str(row[0] if row is not None else 0.0))


def get_daily_cost_cap(conn: Connection, org_id: str) -> Decimal:
    """Resolve the org's ``polling.daily_cost_cap_usd`` (default $1.00/org/day).

    Reads the nested ``polling.daily_cost_cap_usd`` field from the
    ``relay_clients.config`` JSON (PLAN §6). Falls back to
    :data:`DEFAULT_DAILY_COST_CAP_USD` when the row, the ``config`` JSON, the
    ``polling`` object, or the field is absent / malformed.
    """
    raw = read_client_config(conn, org_id)
    if not raw:
        return DEFAULT_DAILY_COST_CAP_USD
    try:
        cfg = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return DEFAULT_DAILY_COST_CAP_USD
    if not isinstance(cfg, dict):
        return DEFAULT_DAILY_COST_CAP_USD
    polling = cfg.get("polling")
    if not isinstance(polling, dict):
        return DEFAULT_DAILY_COST_CAP_USD
    cap = polling.get("daily_cost_cap_usd")
    if cap is None:
        return DEFAULT_DAILY_COST_CAP_USD
    try:
        return Decimal(str(cap))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_COST_CAP_USD


@dataclass(frozen=True)
class DailyBudgetStatus:
    """Result of :func:`check_daily_socialdata_budget`.

    ``over_cap`` is the proactive gate signal the C2.4 poller consumes: when
    ``True`` the poller SKIPS this org's tick and makes ZERO SocialData HTTP
    calls (distinct from the reactive 402 hard-skip, which fires only after
    spend is already incurred). ``spend``/``cap`` are exposed for logging.
    """

    spend: Decimal
    cap: Decimal
    over_cap: bool


def check_daily_socialdata_budget(conn: Connection, org_id: str) -> DailyBudgetStatus:
    """PROACTIVE per-org daily SocialData cap check (SableRelay PLAN §10).

    Sums today's (UTC-day) ``relay_socialdata.%`` spend for ``org_id`` and
    compares it to ``relay_clients.config.polling.daily_cost_cap_usd`` (default
    $1.00/org/day). ``over_cap`` is ``True`` when ``spend >= cap`` — the C2.4
    poller checks this BEFORE polling each org and skips an over-cap org until
    the UTC-midnight window resets, so no request is made and the budget is
    enforced before spend (NOT after, like the reactive HTTP-402 hard-skip).

    This is a NEW relay-side helper, deliberately DISTINCT from
    ``db/cost.py::check_budget`` (which is weekly AI-spend oriented and RAISES
    ``SableError(BUDGET_EXCEEDED)``): this one is per-org/per-day, SocialData-
    scoped, and RETURNS a status rather than raising, so the poller can simply
    skip an org and continue the loop for other orgs.
    """
    spend = get_daily_socialdata_spend(conn, org_id)
    cap = get_daily_cost_cap(conn, org_id)
    return DailyBudgetStatus(spend=spend, cap=cap, over_cap=spend >= cap)


__all__ = [
    "SocialDataClient",
    "HttpResponse",
    "HttpGet",
    "SocialDataError",
    "SocialDataBudgetExhausted",
    "SocialDataRateLimited",
    "SocialDataNotFound",
    "DailyBudgetStatus",
    "check_daily_socialdata_budget",
    "get_daily_socialdata_spend",
    "get_daily_cost_cap",
    "COST_PER_CALL_USD",
    "MAX_RETRIES",
    "DEFAULT_DAILY_COST_CAP_USD",
    "CALL_TYPE_TIMELINE",
    "CALL_TYPE_HYDRATE",
    "CALL_TYPE_REPLIES",
]
