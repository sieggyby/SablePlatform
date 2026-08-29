"""The relay GC window must be decided by the clock, not by the separator character.

Every relay timestamp column is TEXT, and two writers fill them in two spellings:

* the ``server_default=func.now()`` in ``db/schema.py`` writes ``'2026-07-30 12:00:00+00'``
* ``relay/db._utc_now_iso`` writes ``'2026-07-30T12:00:00Z'``

``relay_tweets.fetched_at`` takes both, from ``relay/db.py:576`` and ``relay/db.py:3172``.
The GC helpers used to compare those TEXT values against a Python-formatted ISO-Z cutoff.
Space is 0x20 and ``T`` is 0x54, so the space-form spelling sorts BELOW every ISO-Z cutoff
on the same calendar day, and a row up to a day NEWER than the cutoff was deleted.

Reformatting the cutoff does not fix it, it only swaps the victim: against a space-form
cutoff the ``T`` spelling sorts above and is never collected. Only comparing real instants
on both sides is correct for both spellings, which is what ``compat.ts_before`` emits.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from sable_platform.db.compat import ts_at_or_after, ts_before
from sable_platform.relay import db as relay_db

# The two spellings that reach these columns. SQLite's CURRENT_TIMESTAMP writes the first
# and ``_utc_now_iso`` writes the second. PostgreSQL's ``now()`` writes a third,
# ``'... 12:00:00+00'``, which only a PostgreSQL file ever holds -- see
# ``test_sqlite_cannot_parse_the_postgres_offset_spelling`` for that recorded limit.
SPACE_FORM = "%Y-%m-%d %H:%M:%S.%f"
ISO_Z_FORM = "%Y-%m-%dT%H:%M:%S.%fZ"


def _survivor_offset(retention_days: int) -> timedelta:
    """How far after the cutoff to place a row that must survive.

    The bug only shows while the row and the cutoff share a UTC calendar day: once the day
    digits differ, the TEXT comparison reaches a real decision before it ever reaches the
    separator. So the offset is capped at whatever room is left in the cutoff's own day.

    Timestamps here carry MICROSECONDS for that reason. At second precision a cutoff landing
    at 23:59:59 leaves under a second of room, the survivor formats to the same string as the
    cutoff, and a strict comparison excludes it -- the test would have to skip. SQLite's
    julianday reads a fractional part, verified in
    test_sqlite_cannot_parse_the_postgres_offset_spelling, so microsecond room is real room.
    """
    cut = datetime.now(timezone.utc) - timedelta(days=retention_days)
    end_of_day = cut.replace(hour=23, minute=59, second=59, microsecond=999999)
    return min(timedelta(hours=4), end_of_day - cut)


def _skip_if_no_room(retention_days: int) -> timedelta:
    """The room is measured in microseconds, so this guard should never fire.

    It is an assertion wearing a skip's clothes: if it ever does fire, the cutoff landed in
    the final two microseconds of a UTC day. Keeping it means a freak run reports honestly
    instead of failing for a reason that has nothing to do with the code.
    """
    room = _survivor_offset(retention_days)
    if room < timedelta(microseconds=2):
        pytest.skip("cutoff landed in the last 2us of a UTC day")
    return room


def _stamp(when: datetime, fmt: str) -> str:
    return when.strftime(fmt)


# ---------------------------------------------------------------------------
# Deterministic, no clock: the predicate itself, on fixed data.
# ---------------------------------------------------------------------------

def test_ts_before_gives_both_spellings_the_same_verdict(sa_conn):
    """Same instant, two spellings, one fate. This is the whole contract.

    No wall clock anywhere: the four rows are placed relative to a cutoff computed inside
    SQLite, so this pins the invariant even on the day boundary that skips the tests below.
    """
    sa_conn.execute(text("CREATE TEMP TABLE fmt_probe (id INTEGER, ts TEXT)"))
    # 'now' minus 10 days is the cutoff. Two rows sit 4h later (must survive), two sit 4h
    # earlier (must be collected), each instant written in both spellings.
    sa_conn.execute(text(
        "INSERT INTO fmt_probe (id, ts) VALUES"
        "  (1, strftime('%Y-%m-%d %H:%M:%S', 'now', '-10 days', '+4 hours')),"
        "  (2, strftime('%Y-%m-%dT%H:%M:%SZ',   'now', '-10 days', '+4 hours')),"
        "  (3, strftime('%Y-%m-%d %H:%M:%S', 'now', '-10 days', '-4 hours')),"
        "  (4, strftime('%Y-%m-%dT%H:%M:%SZ',   'now', '-10 days', '-4 hours'))"
    ))
    pred = ts_before("ts", "cutoff", "sqlite")
    collected = {r[0] for r in sa_conn.execute(
        text(f"SELECT id FROM fmt_probe WHERE {pred}"), {"cutoff": "-10 days"}
    ).fetchall()}
    assert collected == {3, 4}, (
        f"expected the two OLDER rows and only those; got {sorted(collected)}. "
        "A result of {1, 3} means the space spelling sorted below the cutoff; "
        "{3} alone means the T spelling sorted above it."
    )


def test_ts_at_or_after_gives_both_spellings_the_same_verdict(sa_conn):
    """The survival side of the window, which is what gc_orphan_chats asks."""
    sa_conn.execute(text("CREATE TEMP TABLE fmt_probe2 (id INTEGER, ts TEXT)"))
    sa_conn.execute(text(
        "INSERT INTO fmt_probe2 (id, ts) VALUES"
        "  (1, strftime('%Y-%m-%d %H:%M:%S', 'now', '-10 days', '+4 hours')),"
        "  (2, strftime('%Y-%m-%dT%H:%M:%SZ',   'now', '-10 days', '+4 hours')),"
        "  (3, strftime('%Y-%m-%d %H:%M:%S', 'now', '-10 days', '-4 hours')),"
        "  (4, strftime('%Y-%m-%dT%H:%M:%SZ',   'now', '-10 days', '-4 hours'))"
    ))
    pred = ts_at_or_after("ts", "cutoff", "sqlite")
    inside = {r[0] for r in sa_conn.execute(
        text(f"SELECT id FROM fmt_probe2 WHERE {pred}"), {"cutoff": "-10 days"}
    ).fetchall()}
    assert inside == {1, 2}


def test_an_empty_timestamp_is_neither_before_nor_after(sa_conn):
    """NULLIF keeps '' out of the comparison, so a data fault never answers the question.

    Callers that must decide the empty case say so explicitly; ``stuck_run_predicate`` is
    the worked example and it counts empty as stuck.
    """
    sa_conn.execute(text("CREATE TEMP TABLE fmt_probe3 (id INTEGER, ts TEXT)"))
    sa_conn.execute(text("INSERT INTO fmt_probe3 (id, ts) VALUES (1, '')"))
    for pred in (ts_before("ts", "c", "sqlite"), ts_at_or_after("ts", "c", "sqlite")):
        hit = sa_conn.execute(
            text(f"SELECT id FROM fmt_probe3 WHERE {pred}"), {"c": "-10 days"}
        ).fetchall()
        assert hit == [], f"'' answered a comparison: {pred}"


# ---------------------------------------------------------------------------
# The real GC functions, on rows written the way production writes them.
# ---------------------------------------------------------------------------

def _seed_message(conn, chat_row_id: int, stamp: str, org_id: str = "gcfmt") -> int:
    row = conn.execute(text(
        "INSERT INTO relay_messages (org_id, chat_id, platform, external_message_id,"
        " external_user_id, text, received_at)"
        " VALUES (:o, :c, 'telegram', :emi, 'u1', 'hi', :ts) RETURNING id"
    ), {"o": org_id, "c": chat_row_id, "emi": stamp, "ts": stamp}).fetchone()
    return int(row[0])


def _seed_org_and_chat(conn, org_id: str = "gcfmt") -> int:
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"),
                 {"o": org_id})
    conn.execute(text("INSERT INTO relay_clients (org_id) VALUES (:o)"), {"o": org_id})
    row = conn.execute(text(
        "INSERT INTO relay_chats (org_id, platform, chat_id, title)"
        " VALUES (:o, 'telegram', 'c1', 'chat') RETURNING id"
    ), {"o": org_id}).fetchone()
    return int(row[0])


def test_gc_messages_keeps_a_row_newer_than_the_cutoff(sa_conn):
    """The headline case: a message NEWER than the retention cutoff must survive.

    Written in the ``func.now()`` spelling, which is what ``relay_messages.received_at``
    actually holds -- ``relay/db.py:492`` never supplies the column.
    """
    room = _skip_if_no_room(90)
    cut = datetime.now(timezone.utc) - timedelta(days=90)
    chat = _seed_org_and_chat(sa_conn)
    _seed_message(sa_conn, chat, _stamp(cut + room, SPACE_FORM))   # newer: must survive
    _seed_message(sa_conn, chat, _stamp(cut - room, SPACE_FORM))   # older: must go
    sa_conn.commit()

    deleted = relay_db.gc_messages(sa_conn, older_than_days=90)
    sa_conn.commit()

    left = [r[0] for r in sa_conn.execute(
        text("SELECT received_at FROM relay_messages ORDER BY id")).fetchall()]
    assert deleted == 1, (
        f"deleted {deleted} of 2. Deleting both is the TEXT-comparison bug: the space "
        "separator sorts below the ISO-Z cutoff, so a row inside the window is collected."
    )
    assert left == [_stamp(cut + room, SPACE_FORM)]


def test_gc_tweets_raw_payload_treats_both_stored_spellings_alike(sa_conn):
    """``relay_tweets.fetched_at`` holds both spellings, so this column is the real case.

    ``relay/db.py:576`` omits the column and takes the server default; ``relay/db.py:3172``
    binds ``_utc_now_iso()``. Two tweets at the same instant, one spelling each, both inside
    the window: neither may lose its raw payload.
    """
    room = _skip_if_no_room(30)
    cut = datetime.now(timezone.utc) - timedelta(days=30)
    inside = cut + room
    for i, fmt in enumerate((SPACE_FORM, ISO_Z_FORM)):
        sa_conn.execute(text(
            "INSERT INTO relay_tweets (x_id, x_author_id, x_author_handle, text,"
            " fetched_at, raw, source)"
            " VALUES (:x, 'a1', 'h', 't', :ts, '{}', 'sweep')"
        ), {"x": f"fmt{i}", "ts": _stamp(inside, fmt)})
    sa_conn.commit()

    cleared = relay_db.gc_tweets_raw_payload(sa_conn, older_than_days=30)
    sa_conn.commit()

    assert cleared == 0, (
        f"cleared {cleared} of 2 rows that are inside the 30d window. Clearing exactly one "
        "is the signature of the bug: the two spellings of one instant got opposite verdicts."
    )


def test_gc_orphan_chats_keeps_a_chat_a_recent_message_still_points_at(sa_conn):
    """The survival side. A wrong answer here deletes a chat a live message references.

    ``relay_messages.chat_id`` is a foreign key onto ``relay_chats.id``, so this is not a
    row collected early, it is a dangling reference.
    """
    room = _skip_if_no_room(90)
    cut = datetime.now(timezone.utc) - timedelta(days=90)
    chat = _seed_org_and_chat(sa_conn)
    _seed_message(sa_conn, chat, _stamp(cut + room, SPACE_FORM))   # inside the window
    sa_conn.commit()

    deleted = relay_db.gc_orphan_chats(sa_conn, messages_older_than_days=90)
    sa_conn.commit()

    remaining = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_chats WHERE id = :i"), {"i": chat}).scalar()
    assert deleted == 0 and remaining == 1, (
        "the chat was reclaimed while a message inside the 90d window still FKs it"
    )


def test_gc_processed_updates_keeps_a_row_newer_than_the_cutoff(sa_conn):
    """``processed_at`` is server-default only, so it is always the space spelling."""
    room = _skip_if_no_room(7)
    cut = datetime.now(timezone.utc) - timedelta(days=7)
    for i, when in enumerate((cut + room, cut - room)):
        sa_conn.execute(text(
            "INSERT INTO relay_processed_updates (platform, update_id, processed_at)"
            " VALUES ('telegram', :u, :ts)"
        ), {"u": f"u{i}", "ts": _stamp(when, SPACE_FORM)})
    sa_conn.commit()

    deleted = relay_db.gc_processed_updates(sa_conn, older_than_days=7)
    sa_conn.commit()
    assert deleted == 1
    left = [r[0] for r in sa_conn.execute(
        text("SELECT update_id FROM relay_processed_updates")).fetchall()]
    assert left == ["u0"]


def test_sqlite_cannot_parse_the_postgres_offset_spelling(sa_conn):
    """A recorded limit, not a passing grade: SQLite rejects a two-digit UTC offset.

    ``julianday`` reads ``'...T08:00:00Z'`` and ``'...+00:00'`` and a fractional part, and
    returns NULL for ``'... 08:00:00+00'`` -- exactly what PostgreSQL's ``now()`` renders
    into a TEXT column. A NULL never satisfies the comparison, so such a row would never be
    collected on SQLite.

    Nothing writes that spelling into a SQLite file: SQLite's own ``CURRENT_TIMESTAMP`` has
    no offset and every Python writer uses ``Z`` or ``+00:00``. This test exists so the day
    someone copies a PostgreSQL dump into SQLite, the limit is already written down.
    """
    got = {v: sa_conn.execute(text("SELECT julianday(:v)"), {"v": v}).scalar() for v in (
        "2026-07-30 08:00:00", "2026-07-30T08:00:00Z", "2026-07-30T08:00:00+00:00",
        "2026-07-30 08:00:00.123456", "2026-07-30 08:00:00+00",
    )}
    parses = [v for v, r in got.items() if r is not None]
    assert parses == ["2026-07-30 08:00:00", "2026-07-30T08:00:00Z",
                      "2026-07-30T08:00:00+00:00", "2026-07-30 08:00:00.123456"]
    assert got["2026-07-30 08:00:00+00"] is None
