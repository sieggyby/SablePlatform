"""Flow A poller + Flow D 4.6 reply follow-through (C2.4).

Two responsibilities, both budget-gated, both consuming the C1.2
:class:`SocialDataClient`:

  1. **Flow A — per-enabled-client auto-broadcast poll** (:func:`poll_org`,
     :func:`poll_all_enabled`). For each ``enabled`` relay client, poll the
     org's source-account timeline (``since_id`` cursor = ``last_seen_x_id``),
     hydrate/upsert new tweets into ``relay_tweets``, and enqueue a
     ``relay_publication_jobs`` row to every active broadcast/community binding.

     **PROACTIVE per-org daily cost gate (PLAN §10):** BEFORE polling each org
     the loop calls :func:`check_daily_socialdata_budget(conn, org_id)`. An
     over-cap org (today's UTC-day ``relay_socialdata.%`` spend ≥
     ``polling.daily_cost_cap_usd``, default $1.00) is SKIPPED with **zero**
     SocialData HTTP calls until the UTC-midnight window resets; an under-cap org
     polls normally in the same pass. This is DISTINCT from C1.2's reactive
     HTTP-402 hard-skip (which only fires after spend is already incurred).

  2. **Flow D 4.6 — reply follow-through tracking** (:func:`track_reply_followups`).
     For each open reply notification (not yet ``replied_at``, inside the 24h
     window), poll ``conversation_id:{tweet_id}`` via the C1.2 client, match
     replies against ``relay_member_identities.external_user_id`` where
     ``platform='x'``, and write ``relay_reply_notifications.replied_at`` +
     ``replied_tweet_id``. **Budget-gated**: a per-opportunity call cap
     (``replies_poll_total_calls``, default 6 / 24h) bounds cost, AND the same
     proactive daily-cap gate applies — without this the ``relay_reply_notifications``
     table ships unwritten/dead.

Every SocialData call goes through the C1.2 client (cache + 402/429 + cursor
dedupe + cost logging). DB writes (cursor advance, tweet upsert, job enqueue,
reply-followthrough) happen INSIDE ``immediate_txn`` AFTER the external fetch
returns — no SocialData call ever happens inside a ``BEGIN IMMEDIATE`` (the C2.2
invariant). The clock is injectable for deterministic tests.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy.engine import Connection

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.txn import immediate_txn
from sable_platform.relay.feed import canonical
from sable_platform.relay.socialdata import (
    SocialDataBudgetExhausted,
    SocialDataClient,
    SocialDataError,
    SocialDataRateLimited,
    check_daily_socialdata_budget,
)

logger = logging.getLogger(__name__)

# PLAN §10 / Appendix 4.6: per-opportunity reply-poll call cap (default 6 / 24h).
DEFAULT_REPLIES_POLL_TOTAL_CALLS = 6


# ---------------------------------------------------------------------------
# Flow A — per-enabled-client poll
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PollResult:
    """Outcome of polling ONE org (for the loop + tests)."""

    org_id: str
    skipped_over_cap: bool  # True iff the proactive daily cap skipped this org
    polled: bool  # True iff a SocialData timeline fetch happened
    new_tweets: int = 0
    jobs_enqueued: int = 0
    error: str | None = None


def _resolve_source_x_id(conn: Connection, org_id: str, config: str | None) -> str | None:
    """Resolve the NUMERIC X user id to poll the source-account timeline.

    The timeline endpoint needs the numeric id (best-practices §2; a screen name
    404s). PLAN §6 carries it in ``config.polling.source_x_user_id`` when known;
    if absent we cannot poll (the handle alone is insufficient) and skip. (A
    later chunk resolves handle→id; here we read the configured id.)
    """
    if not config:
        return None
    try:
        cfg = json.loads(config)
    except (TypeError, ValueError):
        return None
    if not isinstance(cfg, dict):
        return None
    polling = cfg.get("polling")
    if not isinstance(polling, dict):
        return None
    uid = polling.get("source_x_user_id")
    return str(uid) if uid is not None else None


def poll_org(
    conn: Connection,
    client: SocialDataClient,
    org_row: dict,
) -> PollResult:
    """Poll ONE enabled org (Flow A), gated by the proactive daily cost cap.

    Steps:
      1. **Proactive gate**: ``check_daily_socialdata_budget`` — if over cap,
         return immediately with ``skipped_over_cap=True`` and make ZERO calls.
      2. Resolve the numeric source X id; if absent, skip (cannot poll).
      3. Fetch the timeline (``since_id`` = ``last_seen_x_id``) via the C1.2
         client (which applies cursor dedupe + cost logging + 402/429).
      4. For each new tweet: upsert into ``relay_tweets`` and enqueue a
         publication job to every active broadcast/community binding.
      5. Advance the ``last_seen_x_id`` cursor + stamp ``last_polled_at``.
    """
    org_id = org_row["org_id"]

    # --- 1. PROACTIVE per-org daily cost gate (BEFORE any fetch) ---
    status = check_daily_socialdata_budget(conn, org_id)
    if status.over_cap:
        logger.info(
            "relay poller: org %s over daily cap (spend=%s >= cap=%s) — skipping, zero calls",
            org_id,
            status.spend,
            status.cap,
        )
        return PollResult(org_id=org_id, skipped_over_cap=True, polled=False)

    # --- 2. Resolve the numeric source X id ---
    source_x_id = _resolve_source_x_id(conn, org_id, org_row.get("config"))
    if source_x_id is None:
        logger.debug("relay poller: org %s has no source_x_user_id; skipping poll", org_id)
        return PollResult(org_id=org_id, skipped_over_cap=False, polled=False)

    since_id = org_row.get("last_seen_x_id")

    # --- 3. External fetch (OUTSIDE any txn) ---
    try:
        tweets = client.fetch_timeline(org_id, source_x_id, since_id=since_id)
    except SocialDataBudgetExhausted:
        # Reactive 402 latch fired mid-poll: record the error, no further calls.
        with immediate_txn(conn):
            relay_db.update_poll_cursor(conn, org_id, last_error="socialdata 402 (balance exhausted)")
        return PollResult(
            org_id=org_id, skipped_over_cap=False, polled=True,
            error="socialdata_402",
        )
    except SocialDataRateLimited:
        with immediate_txn(conn):
            relay_db.update_poll_cursor(conn, org_id, last_error="socialdata 429 (rate limited)")
        return PollResult(org_id=org_id, skipped_over_cap=False, polled=True, error="socialdata_429")
    except SocialDataError as exc:
        with immediate_txn(conn):
            relay_db.update_poll_cursor(conn, org_id, last_error=f"socialdata error: {exc}")
        return PollResult(org_id=org_id, skipped_over_cap=False, polled=True, error=str(exc))

    # --- 4. Hydrate/upsert + enqueue (DB only, inside one txn) ---
    bindings = relay_db.list_active_destination_bindings(conn, org_id)
    new_tweets = 0
    jobs_enqueued = 0
    max_x_id: int | None = None

    with immediate_txn(conn):
        for tw in tweets:
            x_id = str(tw.get("id_str") or tw.get("id") or "")
            if not x_id:
                continue
            handle = _tweet_handle(tw) or org_id
            media = tw.get("media_urls") if isinstance(tw.get("media_urls"), list) else []
            conv = tw.get("conversation_id_str") or tw.get("conversation_id")
            in_reply = tw.get("in_reply_to_status_id_str") or tw.get("in_reply_to_status_id")
            tweet_row_id = relay_db.upsert_tweet(
                conn,
                x_id=x_id,
                x_author_handle=handle,
                x_author_id=_tweet_author_id(tw),
                text_body=tw.get("full_text") or tw.get("text"),
                media_urls_json=json.dumps(media),
                is_reply=in_reply is not None,
                in_reply_to_x_id=str(in_reply) if in_reply is not None else None,
                conversation_x_id=str(conv) if conv is not None else None,
                raw_json=json.dumps(tw),
            )
            new_tweets += 1
            as_int = _to_int(x_id)
            if as_int is not None and (max_x_id is None or as_int > max_x_id):
                max_x_id = as_int
            for b in bindings:
                job_id = relay_db.enqueue_publication_job(
                    conn,
                    org_id=org_id,
                    tweet_id=tweet_row_id,
                    destination_platform=b["platform"],
                    destination_chat_id=b["chat_id"],
                )
                if job_id is not None:
                    jobs_enqueued += 1

        # --- 5. Advance cursor + stamp last_polled_at (clear last_error) ---
        relay_db.update_poll_cursor(
            conn,
            org_id,
            last_seen_x_id=str(max_x_id) if max_x_id is not None else None,
            last_error=None,
        )

    return PollResult(
        org_id=org_id,
        skipped_over_cap=False,
        polled=True,
        new_tweets=new_tweets,
        jobs_enqueued=jobs_enqueued,
    )


def poll_all_enabled(conn: Connection, client: SocialDataClient) -> list[PollResult]:
    """Poll every enabled relay client (Flow A), each behind the proactive cap.

    The loop is deterministic (ordered by org_id). An over-cap org is skipped
    with zero calls while an under-cap org in the SAME pass polls normally —
    the exact behaviour the C2.4 per-org-cap test asserts.
    """
    results: list[PollResult] = []
    for org_row in relay_db.list_enabled_clients_for_poll(conn):
        results.append(poll_org(conn, client, org_row))
    return results


# ---------------------------------------------------------------------------
# Flow D 4.6 — reply follow-through tracking (budget-gated, per-opportunity cap)
# ---------------------------------------------------------------------------
@dataclass
class ReplyTrackResult:
    """Outcome of the 4.6 reply-tracking pass for ONE org."""

    org_id: str
    skipped_over_cap: bool
    calls_made: int = 0
    followthroughs_recorded: int = 0
    notifications_checked: int = 0
    matched_notification_ids: list[int] = field(default_factory=list)


def _replies_poll_cap(conn: Connection, org_id: str) -> int:
    """Resolve the per-opportunity reply-poll call cap (default 6 / 24h)."""
    raw = relay_db.read_client_config(conn, org_id)
    if not raw:
        return DEFAULT_REPLIES_POLL_TOTAL_CALLS
    try:
        cfg = json.loads(raw)
    except (TypeError, ValueError):
        return DEFAULT_REPLIES_POLL_TOTAL_CALLS
    reply = cfg.get("reply") if isinstance(cfg, dict) else None
    if isinstance(reply, dict):
        val = reply.get("replies_poll_total_calls")
        if isinstance(val, int) and val > 0:
            return val
    return DEFAULT_REPLIES_POLL_TOTAL_CALLS


def track_reply_followups(
    conn: Connection,
    client: SocialDataClient,
    org_id: str,
    *,
    within_hours: int = 24,
) -> ReplyTrackResult:
    """Flow D 4.6: detect opted-in members' replies and write the follow-through.

    Budget-gated two ways:
      * the PROACTIVE daily cap (``check_daily_socialdata_budget``) — over-cap →
        zero calls;
      * a per-opportunity call cap (``replies_poll_total_calls``, default 6 /
        24h) that bounds how many ``conversation_id`` polls run this pass.

    For each open notification (no ``replied_at``, inside ``within_hours``):
    poll ``conversation_id:{tweet_id}`` once, match each reply's author X user id
    against the notified member's ``relay_member_identities`` (platform='x'), and
    if matched write ``replied_at`` + the matched reply's ``x_id``.
    """
    result = ReplyTrackResult(org_id=org_id, skipped_over_cap=False)

    status = check_daily_socialdata_budget(conn, org_id)
    if status.over_cap:
        result.skipped_over_cap = True
        logger.info("relay 4.6: org %s over daily cap — skipping reply tracking", org_id)
        return result

    cap = _replies_poll_cap(conn, org_id)
    notifications = relay_db.list_open_reply_notifications(
        conn, org_id, within_hours=within_hours
    )
    result.notifications_checked = len(notifications)

    for notif in notifications:
        if result.calls_made >= cap:
            logger.info(
                "relay 4.6: org %s hit per-opportunity poll cap (%s) — stopping",
                org_id,
                cap,
            )
            break

        member_x_id = relay_db.get_member_x_user_id(conn, int(notif["member_id"]))
        if member_x_id is None:
            # No linked X identity → reply cannot be detected; do not spend a call.
            continue

        conversation_x_id = notif["conversation_x_id"]
        try:
            replies = client.fetch_conversation_replies(org_id, str(conversation_x_id))
        except SocialDataBudgetExhausted:
            logger.warning("relay 4.6: org %s socialdata 402 — halting reply tracking", org_id)
            break
        except (SocialDataRateLimited, SocialDataError) as exc:
            logger.info("relay 4.6: org %s reply poll error %s — continuing", org_id, exc)
            result.calls_made += 1
            continue
        result.calls_made += 1

        matched_reply_x_id = _match_reply_author(replies, member_x_id)
        if matched_reply_x_id is None:
            continue

        with immediate_txn(conn):
            wrote = relay_db.mark_reply_followed_through(
                conn,
                int(notif["notification_id"]),
                replied_tweet_id=matched_reply_x_id,
            )
        if wrote:
            result.followthroughs_recorded += 1
            result.matched_notification_ids.append(int(notif["notification_id"]))

    return result


# ---------------------------------------------------------------------------
# Tweet-field extraction helpers (tolerant of SocialData shape variance, §14)
# ---------------------------------------------------------------------------
def _tweet_handle(tw: dict) -> str | None:
    user = tw.get("user") if isinstance(tw.get("user"), dict) else {}
    if isinstance(user, dict):
        handle = user.get("screen_name") or user.get("username")
        if isinstance(handle, str) and handle:
            return handle
    handle = tw.get("screen_name")
    return handle if isinstance(handle, str) and handle else None


def _tweet_author_id(tw: dict) -> str | None:
    user = tw.get("user") if isinstance(tw.get("user"), dict) else {}
    if isinstance(user, dict):
        uid = user.get("id_str") or user.get("id")
        if uid is not None:
            return str(uid)
    uid = tw.get("author_id") or tw.get("user_id")
    return str(uid) if uid is not None else None


def _reply_author_x_id(reply: dict) -> str | None:
    """Extract the replying author's X user id (stable id, never handle — §10)."""
    user = reply.get("user") if isinstance(reply.get("user"), dict) else {}
    if isinstance(user, dict):
        uid = user.get("id_str") or user.get("id")
        if uid is not None:
            return str(uid)
    uid = reply.get("author_id") or reply.get("user_id")
    return str(uid) if uid is not None else None


def _match_reply_author(replies: list[dict], member_x_id: str) -> str | None:
    """Return the matched reply's x_id if any reply is authored by ``member_x_id``.

    Matches on the stable X user id (NOT handle — handles change, per §10 /
    Appendix). Returns the FIRST matching reply's x_id, or ``None``.
    """
    for reply in replies:
        if _reply_author_x_id(reply) == str(member_x_id):
            x_id = str(reply.get("id_str") or reply.get("id") or "")
            if x_id:
                return x_id
    return None


def _to_int(value) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


__all__ = [
    "PollResult",
    "ReplyTrackResult",
    "poll_org",
    "poll_all_enabled",
    "track_reply_followups",
    "DEFAULT_REPLIES_POLL_TOTAL_CALLS",
    "canonical",
]
