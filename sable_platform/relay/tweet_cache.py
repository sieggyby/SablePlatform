"""The SHARED SocialData cache (mig 082) — Layer A (tweet-by-id) + Layer B (closed windows).

The cross-system dedup layer of POSTED_REPLY_DETECTION_AND_SHARED_CACHE_PLAN.md §4:
`relay_tweets` is the substrate BOTH the reply stack and Cult Grader read-before-fetch
and write-through, so the same tweet is never paid for twice across systems.

LAYER A — tweet-by-id, with the PLATEAU rule (distinct from the sweep's 6h whole-row
TTL in ``relay.db.get_cached_relay_tweet``, which stays untouched):
  * STATIC fields (text/author/created/media) are immutable — servable whenever a row
    exists with a ``raw`` payload.
  * ENGAGEMENT is time-sensitive: served from cache iff EITHER the row was fetched
    recently (``FRESH_TTL_HOURS``) OR the tweet is PLATEAUED — posted ≥ ``PLATEAU_DAYS``
    ago (engagement is ~80–90% in by 48h and effectively final by two weeks). A row
    with UNKNOWN ``posted_at`` (pre-082) is never treated as plateaued (fail-open to a
    live fetch, never a stale serve).
  * ``get_cached_tweet_raw`` returns the stored SocialData payload so cache hits are
    drop-in replacements for a ``/twitter/tweets/{id}`` response.

LAYER B — closed search windows: a date-bounded search over a PAST window is FINAL
(no new tweets appear in a window that is over). ``mark_window_complete`` records the
result-set (x_ids); ``get_completed_window`` reuses it by hydrating the raw payloads
from ``relay_tweets`` — ANY missing/raw-less id is a MISS (fail-open to a live fetch,
never a silently-partial result). The open/current window is never cacheable
(``mark_window_complete`` refuses a window whose end is in the future).

Cost accounting: hits are $0 by construction (no fetch happens, so nothing logs a
cost row — misses bill exactly as today). Callers surface hit/miss COUNTS in their
own run stats/logs; no new cost_events tags.

Every reader is read-only + fail-safe (None/miss on anything); writers require the
caller's ``immediate_txn`` + commit, matching the relay/db.py conventions.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

# Engagement is effectively FINAL this long after posting (plan §4 Layer A).
PLATEAU_DAYS = 14
# A row fetched this recently serves in full regardless of age (matches the sweep's
# read-through TTL convention so the two layers agree on "fresh").
FRESH_TTL_HOURS = 6


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str | None) -> datetime | None:
    """ISO datetime OR bare date ('2026-06-28' — Cult Grader window bounds) → aware
    UTC datetime. Naive values are TREATED AS UTC (never compared naive-vs-aware)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def normalize_query(query: str) -> str:
    """The Layer-B cache key: whitespace-collapsed, lowercased. Twitter search is
    case-insensitive for operators/handles, so 'FROM:X' and 'from:x' are one window."""
    return " ".join(str(query).split()).lower()


def parse_tweet_posted_at(raw_tweet: dict) -> str | None:
    """The tweet's REAL creation time from a SocialData payload → ISO-Z, or None.
    SocialData uses ``tweet_created_at`` (ISO); legacy shapes may nest under
    ``legacy.created_at`` (Twitter's ctime format — parsed best-effort)."""
    val = raw_tweet.get("tweet_created_at") or raw_tweet.get("created_at")
    if not val and isinstance(raw_tweet.get("legacy"), dict):
        val = raw_tweet["legacy"].get("created_at")
    if not val:
        return None
    dt = _parse_iso(val)
    if dt is None:
        try:  # Twitter ctime: 'Wed Oct 10 20:19:24 +0000 2018'
            dt = datetime.strptime(str(val), "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            return None
    return _iso(dt.astimezone(timezone.utc))


def _tweet_id(raw: dict) -> str | None:
    tid = raw.get("id_str") or raw.get("id")
    return str(tid) if tid else None


def _engagement(raw: dict) -> dict:
    return {
        "likes": raw.get("favorite_count") or raw.get("like_count") or 0,
        "retweets": raw.get("retweet_count") or 0,
        "replies": raw.get("reply_count") or 0,
        "quotes": raw.get("quote_count") or 0,
        "views": raw.get("views_count") or raw.get("view_count") or 0,
    }


def upsert_socialdata_tweets(
    conn: Connection, raw_tweets: list[dict], *, source: str
) -> dict[str, int]:
    """Write-through a batch of raw SocialData tweet payloads → ``{x_id: internal id}``.
    Normalizes author/reply/engagement/posted_at from the payload; skips malformed
    entries (no id / no author). The caller MUST be inside an ``immediate_txn``."""
    from sable_platform.relay.db import upsert_relay_tweet

    out: dict[str, int] = {}
    for t in raw_tweets or []:
        if not isinstance(t, dict):
            continue
        tid = _tweet_id(t)
        user = t.get("user") if isinstance(t.get("user"), dict) else {}
        handle = user.get("screen_name") or t.get("author_handle")
        if not tid or not handle:
            continue
        try:
            out[tid] = upsert_relay_tweet(
                conn,
                x_id=tid,
                x_author_handle=str(handle),
                x_author_id=(str(user.get("id_str") or user.get("id"))
                             if (user.get("id_str") or user.get("id")) else None),
                text_body=t.get("full_text") or t.get("text"),
                is_reply=bool(t.get("in_reply_to_status_id_str")),
                in_reply_to_x_id=(str(t["in_reply_to_status_id_str"])
                                  if t.get("in_reply_to_status_id_str") else None),
                conversation_x_id=(str(t.get("conversation_id_str") or t.get("conversation_id"))
                                   if (t.get("conversation_id_str") or t.get("conversation_id"))
                                   else None),
                raw_json=json.dumps(t, default=str),
                engagement_json=json.dumps(_engagement(t)),
                lang=t.get("lang"),
                author_followers=(int(user["followers_count"])
                                  if isinstance(user.get("followers_count"), int) else None),
                posted_at=parse_tweet_posted_at(t),
                source=source,
            )
        except Exception:  # noqa: BLE001 — one malformed tweet never sinks the batch
            logger.warning("tweet_cache: write-through failed for id=%s", tid, exc_info=True)
    return out


def _servable(row_fetched_at: str | None, row_posted_at: str | None, now: datetime) -> bool:
    """The Layer-A serve rule: fresh-enough fetch OR plateaued age. Unknown posted_at
    is NEVER plateaued (pre-082 rows fail open to a live fetch)."""
    fetched = _parse_iso(row_fetched_at)
    if fetched is not None and now - fetched <= timedelta(hours=FRESH_TTL_HOURS):
        return True
    posted = _parse_iso(row_posted_at)
    return posted is not None and now - posted >= timedelta(days=PLATEAU_DAYS)


def get_cached_tweet_raw(
    conn: Connection, x_id: str, *, now: datetime | None = None
) -> dict | None:
    """Layer-A read: the stored raw SocialData payload for ``x_id`` iff the serve rule
    holds (fresh fetch OR plateaued) AND a raw payload exists — else None (miss).
    A cache hit is a drop-in replacement for a ``/twitter/tweets/{id}`` response."""
    hits = get_cached_tweets_raw(conn, [x_id], now=now)
    return hits.get(str(x_id))


def get_cached_tweets_raw(
    conn: Connection, x_ids: list[str], *, now: datetime | None = None
) -> dict[str, dict]:
    """Batch Layer-A read → ``{x_id: raw payload}`` for the SERVABLE subset only.
    Read-only; unknown ids / unservable rows are simply absent (the caller fetches)."""
    ids = [str(i) for i in x_ids if i]
    if not ids:
        return {}
    now = now or _now()
    placeholders = ", ".join(f":id{i}" for i in range(len(ids)))
    params = {f"id{i}": v for i, v in enumerate(ids)}
    rows = conn.execute(
        text(
            f"SELECT x_id, raw, fetched_at, posted_at FROM relay_tweets "
            f"WHERE x_id IN ({placeholders})"
        ),
        params,
    ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        x_id, raw, fetched_at, posted_at = str(r[0]), r[1], r[2], r[3]
        if not raw or not _servable(fetched_at, posted_at, now):
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            out[x_id] = payload
    return out


def mark_window_complete(
    conn: Connection,
    *,
    query: str,
    window_start: str,
    window_end: str,
    x_ids: list[str],
    source: str,
    now: datetime | None = None,
) -> bool:
    """Layer-B write: record that the (query, window) search is COMPLETE with this
    result-set. REFUSES (returns False, writes nothing) when the window end is in the
    future or unparseable — an OPEN window is never final. First writer wins (a repeat
    mark of the same window is a no-op — closed windows are immutable by definition).
    The caller MUST be inside an ``immediate_txn`` and should have write-through'd the
    tweets FIRST (reuse hydrates through relay_tweets)."""
    now = now or _now()
    end = _parse_iso(window_end)
    if end is None or end > now:
        logger.warning(
            "tweet_cache: refusing to mark OPEN/unparseable window (%s .. %s) for %r",
            window_start, window_end, query,
        )
        return False
    qn = normalize_query(query)
    existing = conn.execute(
        text(
            "SELECT id FROM relay_search_windows "
            "WHERE query_norm = :q AND window_start = :ws AND window_end = :we"
        ),
        {"q": qn, "ws": window_start, "we": window_end},
    ).fetchone()
    if existing is not None:
        return True  # already final — immutable
    uniq = list(dict.fromkeys(str(i) for i in x_ids if i))
    conn.execute(
        text(
            "INSERT INTO relay_search_windows "
            "(query_norm, window_start, window_end, completed_at, result_count, "
            " result_ids_json, source) "
            "VALUES (:q, :ws, :we, :now, :n, :ids, :src)"
        ),
        {"q": qn, "ws": window_start, "we": window_end, "now": _iso(now),
         "n": len(uniq), "ids": json.dumps(uniq), "src": source},
    )
    return True


def get_completed_window(
    conn: Connection, *, query: str, window_start: str, window_end: str
) -> list[dict] | None:
    """Layer-B read: the full raw result-set of a previously-completed (query, window)
    search, hydrated through ``relay_tweets`` — or None (miss). ANY member id whose
    raw payload is missing → None (fail-open to a live fetch; never silently partial).
    Read-only. NOTE: hydration bypasses the Layer-A serve rule on purpose — a closed
    window's members are final AS A SET; their engagement is whatever the write-through
    recorded (callers needing live engagement on a young tweet re-fetch it by id)."""
    qn = normalize_query(query)
    row = conn.execute(
        text(
            "SELECT result_ids_json FROM relay_search_windows "
            "WHERE query_norm = :q AND window_start = :ws AND window_end = :we"
        ),
        {"q": qn, "ws": window_start, "we": window_end},
    ).fetchone()
    if row is None:
        return None
    try:
        ids = json.loads(row[0] or "[]")
    except (TypeError, ValueError):
        return None
    if not isinstance(ids, list):
        return None
    ids = [str(i) for i in ids]
    if not ids:
        return []
    placeholders = ", ".join(f":id{i}" for i in range(len(ids)))
    params = {f"id{i}": v for i, v in enumerate(ids)}
    rows = conn.execute(
        text(f"SELECT x_id, raw FROM relay_tweets WHERE x_id IN ({placeholders})"),
        params,
    ).fetchall()
    by_id: dict[str, dict] = {}
    for r in rows:
        try:
            payload = json.loads(r[1]) if r[1] else None
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            by_id[str(r[0])] = payload
    if len(by_id) < len(set(ids)):
        return None  # a member is missing/raw-less — treat the whole window as a miss
    return [by_id[i] for i in dict.fromkeys(ids)]
