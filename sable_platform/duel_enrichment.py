"""Weekly duel-pool enrichment — promote community tweets into the /duel game.

The sable-roles /duel game serves ``content_candidates`` of kind ``community_tweet``.
Rather than a manual harvest, this promotes TIG-relevant, *popped* tweets that OTHER
Sable tools have already fetched into the shared SocialData cache (``relay_tweets`` —
filled continuously by the reply sweep + Cult Grader). It is therefore FREE: it reads
the existing cache, never spends new SocialData credits.

Design choices:
- TEXT-only. It never sets ``image_url`` — meme/image cards are HUMAN-curated
  (/duel-submit + SableTracking meme drops), never auto-classified (a machine can't
  cheaply tell a meme from a chart, and the operator asked not to pay a model to try).
- Relevance-gated: a tweet qualifies if its text matches one of the org's ``terms``
  (cashtag/keywords) OR its author is on the org's ``authors`` allowlist. Off-topic
  tweets never enter a client channel (the 0x0Pika lesson).
- Popped-gated: only tweets whose weighted engagement clears ``min_popped`` — the game
  is "which popped?", so a dead tweet is not content.
- Deduped on tweet id across ALL candidate statuses (never re-add a tweet already
  seen, even one previously rejected/kept), and capped per run.
"""
from __future__ import annotations

import html as _html
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as _sa_text

from sable_platform.db.content_deck import upsert_candidate
from sable_platform.relay.bot.txn import immediate_txn

# X handle shape — a candidate whose author isn't handle-shaped is dropped by the bot's
# render whitelist, so don't even promote it.
_HANDLE_OK = lambda h: isinstance(h, str) and 1 <= len(h) <= 15 and h.replace("_", "").isalnum()
_ENGAGEMENT_KEYS = ("likes", "retweets", "replies", "quotes")


def _popped_score(eng: dict) -> int:
    """The ingest-side popped formula (retweets DOUBLE, views excluded) — identical to the
    bot's reveal score so the enrichment gate and the reveal agree."""
    return int(eng.get("likes", 0)) + 2 * int(eng.get("retweets", 0)) + \
        int(eng.get("replies", 0)) + int(eng.get("quotes", 0))


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    s = str(value).strip().replace("T", " ").rstrip("Z")[:19]
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        # SocialData's tweet_created_at is e.g. "Wed Jun 04 14:28:22 +0000 2026"
        try:
            return datetime.strptime(str(value), "%a %b %d %H:%M:%S %z %Y")
        except (ValueError, TypeError):
            return None


def _posted_at(row_posted, raw: dict, eng: dict) -> datetime | None:
    return (_parse_dt(row_posted) or _parse_dt(raw.get("tweet_created_at"))
            or _parse_dt(eng.get("created_at")))


def _existing_x_ids(conn, org_id: str) -> set[str]:
    """Every tweet id ALREADY promoted for this org (any candidate status) — the dedup set.
    community_tweet payloads carry x_id; we read them all (bounded per org)."""
    rows = conn.execute(
        _sa_text("SELECT payload_json FROM content_candidates "
                 "WHERE org_id = :org AND kind = 'community_tweet'"),
        {"org": org_id},
    ).fetchall()
    ids: set[str] = set()
    for r in rows:
        try:
            x = json.loads(r[0]).get("x_id")
        except (ValueError, TypeError):
            x = None
        if x:
            ids.add(str(x))
    return ids


def enrich_duel_pool(
    conn,
    org_id: str,
    *,
    terms: tuple[str, ...],
    authors: tuple[str, ...] = (),
    min_popped: int = 15,
    lookback_days: int = 45,
    max_add: int = 40,
    source_tag: str = "duel_enrich",
    now: str | None = None,
) -> dict:
    """Promote qualifying cached tweets into the org's duel pool. Returns a summary dict.
    Caller need NOT be in a txn — this opens its own immediate_txn for the writes."""
    now_dt = _parse_dt(now) or datetime.now(timezone.utc)
    cutoff = now_dt - timedelta(days=lookback_days)
    now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if not terms and not authors:
        return {"scanned": 0, "added": 0, "note": "no terms/authors configured — skipped"}

    # relevance predicate: (text LIKE any term) OR (author in the allowlist). Bound params
    # only — a config term can never smuggle SQL.
    params: dict = {"org": org_id}
    ors = []
    for i, t in enumerate(terms):
        ors.append(f"LOWER(text) LIKE :term_{i}")
        params[f"term_{i}"] = f"%{str(t).lower()}%"
    for i, a in enumerate(authors):
        ors.append(f"LOWER(x_author_handle) = :auth_{i}")
        params[f"auth_{i}"] = str(a).lower().lstrip("@")
    where_rel = "(" + " OR ".join(ors) + ")" if ors else "1=1"
    rows = conn.execute(
        _sa_text(
            "SELECT x_id, x_author_handle, text, engagement_json, lang, posted_at, raw "
            "FROM relay_tweets "
            f"WHERE {where_rel} AND text IS NOT NULL AND text <> '' "
            "  AND (is_reply IS NULL OR is_reply = false)"
        ),
        params,
    ).fetchall()

    seen = _existing_x_ids(conn, org_id)
    summary = {"scanned": len(rows), "eligible": 0, "added": 0,
               "skip_dup": 0, "skip_engagement": 0, "skip_stale": 0, "skip_bad": 0}
    candidates: list[tuple[int, dict, str]] = []  # (popped, payload, x_id)
    for r in rows:
        x_id = str(r[0]) if r[0] is not None else None
        handle = r[1]
        tweet_text = r[2]
        if not x_id or x_id in seen or not _HANDLE_OK(handle):
            summary["skip_dup" if x_id in seen else "skip_bad"] += 1
            continue
        try:
            eng_raw = json.loads(r[3]) if r[3] else {}
            raw = json.loads(r[6]) if r[6] else {}
        except (ValueError, TypeError):
            summary["skip_bad"] += 1
            continue
        eng = {k: int(eng_raw.get(k, 0) or 0) for k in _ENGAGEMENT_KEYS}
        popped = _popped_score(eng)
        if popped < min_popped:
            summary["skip_engagement"] += 1
            continue
        posted = _posted_at(r[5], raw, eng_raw)
        if posted is not None and posted < cutoff:
            summary["skip_stale"] += 1
            continue
        summary["eligible"] += 1
        payload = {
            "text": _html.unescape(str(tweet_text)),
            "author_handle": handle,
            "author_name": raw.get("user", {}).get("name") if isinstance(raw.get("user"), dict) else handle,
            "x_id": x_id,
            "url": f"https://x.com/{handle}/status/{x_id}",
            "engagement": eng,
            "engagement_as_of": now_iso,
            "lang": (r[4] or "en"),
            "posted_at": posted.strftime("%Y-%m-%dT%H:%M:%SZ") if posted else None,
            "ingest_batch": now_iso[:10],
        }
        candidates.append((popped, payload, x_id))

    # highest-engagement first, capped — the best of the eligible pool enters this run
    candidates.sort(key=lambda c: -c[0])
    with immediate_txn(conn):
        for popped, payload, x_id in candidates[: max(0, max_add)]:
            upsert_candidate(
                conn, org_id=org_id, kind="community_tweet",
                payload_json=json.dumps(payload), source=source_tag,
                dedupe_key=f"ct:{x_id}", score=float(popped), now=now_iso,
            )
            seen.add(x_id)
            summary["added"] += 1
    return summary
