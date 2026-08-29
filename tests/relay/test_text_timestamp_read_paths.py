"""The same defect on the READ side: a text comparison decides before the clock does.

`test_relay_gc_timestamp_formats.py` covers the paths that DELETE. Codex found four that
only read, which a sweep for DELETE and UPDATE missed entirely. Reading is not harmless:

  * `get_cached_relay_tweet` returns a cache MISS for a tweet fetched seconds ago, and the
    caller pays SocialData for a refetch it already had.
  * `member_replied_within` and `team_posted_within` are AutoCM's two "somebody already
    replied, stay quiet" checks. Both were answering no.
  * the weekly digest counts messages into the wrong week, and used `MIN` on a text column
    to find a member's first-ever message.

Every column below carries `server_default=func.now()` and a writer that omits it, so the
stored value is the space spelling while the cutoff is ISO-Z. Space is 0x20 and `T` is 0x54.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from sable_platform.autocm import db as autocm_db
from sable_platform.autocm.digest import analytics
from sable_platform.relay import db as relay_db

# SQLite's CURRENT_TIMESTAMP spelling: space separator, no offset. This is what the
# server default writes into every one of these columns.
SPACE = "%Y-%m-%d %H:%M:%S"
# A fixed clock, so none of this depends on what time the suite runs. Mid-day and mid-week.
PINNED = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)


def _seed_org(conn, org_id="tsread"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id})
    row = conn.execute(text(
        "INSERT INTO relay_chats (org_id, platform, chat_id, title)"
        " VALUES (:o, 'telegram', '-100', 'c') RETURNING id"), {"o": org_id}).fetchone()
    conn.commit()
    return org_id, int(row[0])


def _msg(conn, org_id, chat_id, when: datetime, *, ext="u1", member_id=None, eid=None):
    conn.execute(text(
        "INSERT INTO relay_messages (org_id, chat_id, member_id, platform,"
        " external_message_id, external_user_id, text, received_at)"
        " VALUES (:o, :c, :mid, 'telegram', :e, :ext, 'hi', :ts)"
    ), {"o": org_id, "c": chat_id, "mid": member_id,
        "e": eid or when.strftime("%H%M%S%f"), "ext": ext, "ts": when.strftime(SPACE)})
    conn.commit()


# ---------------------------------------------------------------------------
# AutoCM: the two suppression checks. Clock pinned, so no boundary to dodge.
# ---------------------------------------------------------------------------

def test_member_replied_within_sees_a_message_written_by_the_server_default(sa_conn):
    """A reply five seconds old must suppress. It was invisible.

    `persist_inbound_message` omits `received_at`, so this is the ONLY spelling production
    stores in this column on PostgreSQL. Measured there before the fix: the text comparison
    returned nothing at every window from 60s to 4h, including a message 5 seconds old. The
    strong-skip was not merely inaccurate, it never fired.
    """
    org, chat = _seed_org(sa_conn)
    _msg(sa_conn, org, chat, PINNED - timedelta(seconds=5), ext="other")

    assert autocm_db.member_replied_within(
        sa_conn, chat, seconds=60, exclude_external_user_id="me", now=PINNED
    ) is True, "a reply 5 seconds old did not register; the don't-pile-on skip is dead"


def test_member_replied_within_still_ignores_a_message_outside_the_window(sa_conn):
    """The power check. Making the predicate see everything would pass the test above."""
    org, chat = _seed_org(sa_conn)
    _msg(sa_conn, org, chat, PINNED - timedelta(seconds=600), ext="other")

    assert autocm_db.member_replied_within(
        sa_conn, chat, seconds=60, exclude_external_user_id="me", now=PINNED
    ) is False, "a reply 10 minutes old registered inside a 60 second window"


def test_team_posted_within_sees_a_message_written_by_the_server_default(sa_conn):
    """Founder pre-emption, same column, same failure."""
    org, chat = _seed_org(sa_conn)
    mid = int(sa_conn.execute(text(
        "INSERT INTO relay_members (display_name) VALUES ('F') RETURNING id")).fetchone()[0])
    sa_conn.execute(text(
        "INSERT INTO relay_member_roles (org_id, member_id, role)"
        " VALUES (:o, :m, 'client_team')"), {"o": org, "m": mid})
    _msg(sa_conn, org, chat, PINNED - timedelta(seconds=30), ext="founder", member_id=mid)

    assert autocm_db.team_posted_within(
        sa_conn, org, chat, minutes=5, exclude_external_user_id="me", now=PINNED
    ) is True, "the founder posted 30 seconds ago and pre-emption did not fire"


def test_team_posted_within_still_ignores_a_message_outside_the_window(sa_conn):
    org, chat = _seed_org(sa_conn)
    mid = int(sa_conn.execute(text(
        "INSERT INTO relay_members (display_name) VALUES ('F') RETURNING id")).fetchone()[0])
    sa_conn.execute(text(
        "INSERT INTO relay_member_roles (org_id, member_id, role)"
        " VALUES (:o, :m, 'client_team')"), {"o": org, "m": mid})
    _msg(sa_conn, org, chat, PINNED - timedelta(hours=2), ext="founder", member_id=mid)

    assert autocm_db.team_posted_within(
        sa_conn, org, chat, minutes=5, exclude_external_user_id="me", now=PINNED
    ) is False


# ---------------------------------------------------------------------------
# The weekly digest. week_start is a caller argument, so this is fully deterministic.
# ---------------------------------------------------------------------------

def test_the_weekly_digest_counts_a_message_on_the_first_day_of_its_week(sa_conn):
    """A message on the week's FIRST DAY must be in that week. It was dropped.

    The boundary day is the whole defect, and a fixture that avoids it proves nothing. Once
    the calendar dates differ, the TEXT comparison reaches a real decision before it reaches
    the separator, and both spellings agree. It is only on the bound's own day that the
    separator decides: `'2026-07-27 09:00:00'` against the start bound
    `'2026-07-27T00:00:00Z'` compares space (0x20) to T (0x54) and reads as EARLIER.

    So the digest silently dropped every message sent on day one of the week, one seventh of
    the corpus, and the same on the previous week's first day.
    """
    org, chat = _seed_org(sa_conn)
    week_start = datetime(2026, 7, 27, 0, 0, tzinfo=timezone.utc)     # a Monday
    prev_start = week_start - timedelta(days=7)
    _msg(sa_conn, org, chat, week_start + timedelta(hours=9), ext="a", eid="day1")
    _msg(sa_conn, org, chat, week_start + timedelta(days=3, hours=14), ext="b", eid="mid")
    _msg(sa_conn, org, chat, prev_start + timedelta(hours=9), ext="c", eid="prevday1")

    stats = analytics.volume(sa_conn, 0, week_start, org_id=org)
    assert stats.messages == 2, (
        f"digest counted {stats.messages} of 2. Counting 1 means the day-one message was "
        "sorted below its own week's start bound.")
    assert stats.distinct_members == 2
    assert stats.prev_messages == 1, (
        f"the previous week's day-one message was dropped too: saw {stats.prev_messages}")


def test_the_weekly_digest_orders_mixed_spellings_by_instant(sa_conn):
    """`ORDER BY received_at` on a mixed-spelling column interleaves the week.

    A text sort puts every ISO-Z row above every space row regardless of when they happened,
    so `_week_messages` handed the digest its own week out of order.
    """
    org, chat = _seed_org(sa_conn)
    week_start = datetime(2026, 7, 27, 0, 0, tzinfo=timezone.utc)
    # All four on ONE day, so the date digits cannot decide and the separator must not.
    # Chronological order is 09:00, 11:00, 14:00, 16:00. A text sort puts both space rows
    # above both T rows, which is 11:00, 16:00, 09:00, 14:00 -- inverted in the middle.
    for i, (hour, fmt) in enumerate([(9, "%Y-%m-%dT%H:%M:%SZ"), (11, SPACE),
                                     (14, "%Y-%m-%dT%H:%M:%SZ"), (16, SPACE)]):
        when = week_start + timedelta(days=1, hours=hour)
        sa_conn.execute(text(
            "INSERT INTO relay_messages (org_id, chat_id, platform, external_message_id,"
            " external_user_id, text, received_at)"
            " VALUES (:o, :c, 'telegram', :e, :ext, 'hi', :ts)"
        ), {"o": org, "c": chat, "e": f"o{i}", "ext": f"u{i}", "ts": when.strftime(fmt)})
    sa_conn.commit()

    rows = analytics._week_messages(sa_conn, org, *analytics.week_bounds(week_start))
    got = [r["external_user_id"] for r in rows]
    assert got == ["u0", "u1", "u2", "u3"], f"week returned out of chronological order: {got}"


# ---------------------------------------------------------------------------
# The tweet cache. No clock injection here, so the row is placed against the cutoff.
# ---------------------------------------------------------------------------

def _room_inside_the_cutoff_day(ttl_hours: int) -> timedelta:
    """Place the row after the cutoff and inside the cutoff's own UTC day.

    Once the day digits differ, the text comparison reaches a real decision before it reaches
    the separator, and the test stops exercising the bug.
    """
    cut = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    end_of_day = cut.replace(hour=23, minute=59, second=59, microsecond=999999)
    room = min(timedelta(minutes=20), end_of_day - cut)
    if room < timedelta(seconds=1):
        pytest.skip("cutoff landed in the last second of a UTC day")
    return room


def test_get_cached_relay_tweet_is_a_hit_for_a_server_default_row(sa_conn):
    """A tweet inside its TTL must be a cache HIT. It read as stale and cost a refetch.

    `relay/db.py:576` omits `fetched_at` and takes the default; `relay/db.py:3172` binds
    `_utc_now_iso()`. This column is the one place both spellings are already proven to
    coexist, so it is the case worth pinning.
    """
    room = _room_inside_the_cutoff_day(1)
    fetched = datetime.now(timezone.utc) - timedelta(hours=1) + room
    sa_conn.execute(text(
        "INSERT INTO relay_tweets (x_id, x_author_id, x_author_handle, text, fetched_at, raw)"
        " VALUES ('cache1', 'a', 'h', 't', :ts, '{}')"), {"ts": fetched.strftime(SPACE)})
    sa_conn.commit()

    hit = relay_db.get_cached_relay_tweet(sa_conn, "cache1", ttl_hours=1)
    assert hit is not None, (
        "a tweet fetched inside the TTL read as a miss, so the caller refetches and pays")


def test_get_cached_relay_tweet_is_still_a_miss_past_the_ttl(sa_conn):
    """The power check. A predicate that accepted everything would pass the test above."""
    stale = datetime.now(timezone.utc) - timedelta(hours=3)
    sa_conn.execute(text(
        "INSERT INTO relay_tweets (x_id, x_author_id, x_author_handle, text, fetched_at, raw)"
        " VALUES ('cache2', 'a', 'h', 't', :ts, '{}')"), {"ts": stale.strftime(SPACE)})
    sa_conn.commit()
    assert relay_db.get_cached_relay_tweet(sa_conn, "cache2", ttl_hours=1) is None


def test_fetched_at_is_not_nullable_so_the_old_none_branch_was_unreachable(sa_conn):
    """The `fetched_at is None` branch the fix removed could never have run.

    `relay_tweets.fetched_at` is NOT NULL. The old code tested for None before comparing,
    which read as defensive and was dead. `NULLIF` in the new predicate still handles an
    EMPTY string, which the schema does allow, so nothing was traded away.
    """
    with pytest.raises(Exception) as exc:
        sa_conn.execute(text(
            "INSERT INTO relay_tweets (x_id, x_author_id, x_author_handle, text, fetched_at,"
            " raw) VALUES ('cache3', 'a', 'h', 't', NULL, '{}')"))
    assert "not null" in str(exc.value).lower()
    sa_conn.rollback()

    # The reachable fault, an empty string, is a miss rather than a crash or a false hit.
    sa_conn.execute(text(
        "INSERT INTO relay_tweets (x_id, x_author_id, x_author_handle, text, fetched_at, raw)"
        " VALUES ('cache4', 'a', 'h', 't', '', '{}')"))
    sa_conn.commit()
    assert relay_db.get_cached_relay_tweet(sa_conn, "cache4", ttl_hours=1) is None


def test_the_digest_does_not_count_a_returning_member_as_new(sa_conn):
    """A regression guard, and labelled as one: it passes against the pre-fix tree too.

    Codex asked for direct coverage of the earliest-row rewrite. This is the half that is
    only a guard. A text `MIN` can only pick the wrong row when two candidates share a
    calendar date, and two messages on one day cannot straddle a week boundary, so the text
    `MIN` was never wrong ACROSS weeks. The bug-catching half is the test below it.
    """
    org, chat = _seed_org(sa_conn)
    week_start = datetime(2026, 7, 27, 0, 0, tzinfo=timezone.utc)
    iso = "%Y-%m-%dT%H:%M:%SZ"

    def put(when, ext, fmt):
        sa_conn.execute(text(
            "INSERT INTO relay_messages (org_id, chat_id, platform, external_message_id,"
            " external_user_id, text, received_at)"
            " VALUES (:o, :c, 'telegram', :e, :ext, 'hi', :ts)"
        ), {"o": org, "c": chat, "e": f"{ext}-{when:%j%H}", "ext": ext, "ts": when.strftime(fmt)})

    # u_old: earliest is LAST week, written in the space spelling.
    put(week_start - timedelta(days=3), "u_old", SPACE)
    put(week_start + timedelta(days=2, hours=10), "u_old", iso)
    # u_new: earliest is this week.
    put(week_start + timedelta(days=2, hours=11), "u_new", SPACE)
    sa_conn.commit()

    stats = analytics.volume(sa_conn, 0, week_start, org_id=org)
    assert stats.messages == 2, f"the week holds 2 messages, digest saw {stats.messages}"
    assert stats.new_members == 1, (
        f"new_members={stats.new_members}. 2 means a returning member whose first-ever "
        "message is older than the week was counted as new, which is the text-MIN failure.")


def test_the_digest_counts_one_member_once_when_two_messages_share_a_timestamp(sa_conn):
    """The half that DOES fail against the pre-fix tree.

    The old code decided "is this row the member's first" with
    `m["received_at"] == first_seen`, a string comparison. Two messages carrying the same
    timestamp string both matched, so one member counted as two new members. Matching on the
    row id is exact. Sharing a timestamp is ordinary: `func.now()` inside one transaction
    returns the same value for every row it writes.
    """
    org, chat = _seed_org(sa_conn)
    week_start = datetime(2026, 7, 27, 0, 0, tzinfo=timezone.utc)
    same_instant = (week_start + timedelta(days=2, hours=10)).strftime(SPACE)
    for i in (1, 2):
        sa_conn.execute(text(
            "INSERT INTO relay_messages (org_id, chat_id, platform, external_message_id,"
            " external_user_id, text, received_at)"
            " VALUES (:o, :c, 'telegram', :e, 'twin', 'hi', :ts)"
        ), {"o": org, "c": chat, "e": f"twin{i}", "ts": same_instant})
    sa_conn.commit()

    stats = analytics.volume(sa_conn, 0, week_start, org_id=org)
    assert stats.messages == 2 and stats.distinct_members == 1
    assert stats.new_members == 1, (
        f"new_members={stats.new_members} for ONE member. 2 means both rows matched the "
        "first-ever timestamp string and the same member was counted twice.")
