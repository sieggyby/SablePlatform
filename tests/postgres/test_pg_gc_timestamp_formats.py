"""Live PostgreSQL coverage for the relay GC window and the stuck-run predicate.

``tests/relay/test_relay_gc_timestamp_formats.py`` proves the same contract on SQLite. Two
things only PostgreSQL can answer are here:

  * PostgreSQL's ``now()`` renders into a TEXT column as ``'2026-08-29 15:58:30.5+00'``, a
    two-digit offset that SQLite's ``julianday`` cannot parse at all. The PostgreSQL branch
    casts to ``timestamptz`` instead, so that spelling is the ordinary case here.
  * A single ``''`` in a compared column RAISES on PostgreSQL and takes the whole query
    down. SQLite silently sorts it. ``NULLIF`` is what keeps both dialects working.

The last test in this file records a MEASURED refusal, not a wish: the expression index that
``DEFECTS_FOUND.md`` proposed for ``cost_events.created_at`` cannot be built.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from sable_platform.db.compat import (stuck_run_predicate, ts_at_or_after, ts_before,
                                      ts_order)
from sable_platform.relay import db as relay_db


def _seed_org_and_chat(conn, org_id="gcfmt"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(text("INSERT INTO relay_clients (org_id) VALUES (:o)"), {"o": org_id})
    row = conn.execute(text(
        "INSERT INTO relay_chats (org_id, platform, chat_id, title)"
        " VALUES (:o, 'telegram', 'c1', 'chat') RETURNING id"
    ), {"o": org_id}).fetchone()
    conn.commit()
    return int(row[0])


# ---------------------------------------------------------------------------
# The three spellings, on the dialect that writes the awkward one.
# ---------------------------------------------------------------------------

def test_ts_before_reads_all_three_spellings_on_postgres(postgres_engine):
    """``now()``-rendered, ISO-Z, and naive: one instant, one verdict, three ways to write it.

    The naive row is the one the pinned session timezone protects. ``engine._pin_utc_session``
    runs ``SET TIME ZONE 'UTC'`` on connect, so a naive string resolves to UTC rather than to
    whatever zone the server happens to default to.
    """
    conn = postgres_engine.connect()
    try:
        conn.execute(text("SET TIME ZONE 'UTC'"))
        conn.execute(text("CREATE TEMP TABLE fmt_probe (id int, ts text)"))
        conn.execute(text(
            "INSERT INTO fmt_probe (id, ts)"
            " SELECT 1, to_char(now() - interval '10 days' + interval '4 hours',"
            "                   'YYYY-MM-DD HH24:MI:SS.USOF')"
            " UNION ALL SELECT 2, to_char(now() - interval '10 days' + interval '4 hours',"
            "                   'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
            " UNION ALL SELECT 3, to_char(now() - interval '10 days' + interval '4 hours',"
            "                   'YYYY-MM-DD HH24:MI:SS')"
            " UNION ALL SELECT 4, to_char(now() - interval '10 days' - interval '4 hours',"
            "                   'YYYY-MM-DD HH24:MI:SS.USOF')"
            " UNION ALL SELECT 5, to_char(now() - interval '10 days' - interval '4 hours',"
            "                   'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
            " UNION ALL SELECT 6, to_char(now() - interval '10 days' - interval '4 hours',"
            "                   'YYYY-MM-DD HH24:MI:SS')"
        ))
        pred = ts_before("ts", "cutoff", "postgresql")
        got = {r[0] for r in conn.execute(
            text(f"SELECT id FROM fmt_probe WHERE {pred}"), {"cutoff": "-10 days"}).fetchall()}
        assert got == {4, 5, 6}, f"spellings disagreed: collected {sorted(got)}"

        inv = ts_at_or_after("ts", "cutoff", "postgresql")
        got2 = {r[0] for r in conn.execute(
            text(f"SELECT id FROM fmt_probe WHERE {inv}"), {"cutoff": "-10 days"}).fetchall()}
        assert got2 == {1, 2, 3}
    finally:
        conn.close()


def test_ts_before_survives_an_empty_string_row(postgres_engine):
    """One '' used to raise and take the whole GC query down. NULLIF is why it does not."""
    conn = postgres_engine.connect()
    try:
        conn.execute(text("CREATE TEMP TABLE empty_probe (id int, ts text)"))
        conn.execute(text(
            "INSERT INTO empty_probe VALUES (1, ''), "
            " (2, to_char(now() - interval '20 days', 'YYYY-MM-DD HH24:MI:SS.USOF'))"))
        pred = ts_before("ts", "cutoff", "postgresql")
        got = {r[0] for r in conn.execute(
            text(f"SELECT id FROM empty_probe WHERE {pred}"), {"cutoff": "-10 days"}).fetchall()}
        assert got == {2}

        # And the control: WITHOUT NULLIF the same data raises, which is the symptom the
        # helper exists to remove. If this stops raising, the helper is no longer load-bearing.
        with pytest.raises(Exception) as exc:
            conn.execute(text(
                "SELECT id FROM empty_probe WHERE ts::timestamptz < NOW()")).fetchall()
        assert "invalid input syntax" in str(exc.value).lower()
        conn.rollback()
    finally:
        conn.close()


def test_gc_messages_keeps_a_row_newer_than_the_cutoff_on_postgres(postgres_engine):
    """The real GC function, on rows written the way PostgreSQL's server default writes them."""
    conn = postgres_engine.connect()
    try:
        conn.execute(text("SET TIME ZONE 'UTC'"))
        chat = _seed_org_and_chat(conn)
        for i, delta in enumerate(("+ interval '4 hours'", "- interval '4 hours'")):
            conn.execute(text(
                "INSERT INTO relay_messages (org_id, chat_id, platform, external_message_id,"
                " external_user_id, text, received_at)"
                f" SELECT 'gcfmt', :c, 'telegram', :e, 'u1', 'hi',"
                f" to_char(now() - interval '90 days' {delta}, 'YYYY-MM-DD HH24:MI:SS.USOF')"
            ), {"c": chat, "e": f"m{i}"})
        conn.commit()

        deleted = relay_db.gc_messages(conn, older_than_days=90)
        conn.commit()
        left = [r[0] for r in conn.execute(
            text("SELECT external_message_id FROM relay_messages ORDER BY id")).fetchall()]
        assert deleted == 1 and left == ["m0"], f"deleted={deleted} left={left}"
    finally:
        conn.close()


def test_stuck_run_predicate_counts_an_empty_started_at_on_postgres(postgres_engine):
    """Empty and NULL are the data faults the four views used to disagree about."""
    conn = postgres_engine.connect()
    try:
        conn.execute(text("SET TIME ZONE 'UTC'"))
        conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES ('so','so')"))
        conn.execute(text(
            "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, started_at)"
            # idx_workflow_runs_active_lock is UNIQUE on (org_id, workflow_name) for a
            # running row, so each case needs its own workflow. That lock is the reason a
            # never-timing-out row blocks its workflow forever, which is the whole point.
            " VALUES ('r-empty','so','w_empty','running',''),"
            "        ('r-null','so','w_null','running',NULL),"
            "        ('r-old','so','w_old','running',"
            "           to_char(now() - interval '9 hours','YYYY-MM-DD HH24:MI:SS.USOF')),"
            "        ('r-fresh','so','w_fresh','running',"
            "           to_char(now() - interval '1 minute','YYYY-MM-DD HH24:MI:SS.USOF'))"))
        conn.commit()
        pred = stuck_run_predicate("started_at", "offset", "postgresql")
        got = {r[0] for r in conn.execute(text(
            f"SELECT run_id FROM workflow_runs WHERE status='running' AND {pred}"),
            {"offset": "-2 hours"}).fetchall()}
        assert got == {"r-empty", "r-null", "r-old"}, f"got {sorted(got)}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# DEFECTS_FOUND item 4, recorded as a measurement rather than a plan.
# ---------------------------------------------------------------------------

def test_the_proposed_cost_events_expression_index_cannot_be_built(postgres_engine):
    """PostgreSQL refuses every cast-based expression index on a TEXT timestamp column.

    ``DEFECTS_FOUND.md`` item 4 proposed an index on ``NULLIF(created_at,'')::timestamptz``
    to restore the btree that ``ts_column``'s cast disables. It does not compile.
    ``text::timestamptz`` is STABLE, not IMMUTABLE, because it resolves relative inputs and
    reads the session timezone. ``::timestamp`` and ``AT TIME ZONE`` are refused for the
    same reason.

    An ``IMMUTABLE``-declared wrapper does compile and is roughly 12x faster on 200k rows.
    It is also unsound: the wrapper asserts an immutability the cast does not have, so an
    index built under one session timezone silently disagrees with a fresh evaluation under
    another as soon as ONE stored value lacks an explicit offset. A GC ``DELETE`` reading
    that index keeps or removes the wrong rows and reports success.

    This test exists so the proposal is not made a third time. The real repair is a schema
    migration to a native ``timestamptz`` column, which is separate work.
    """
    conn = postgres_engine.connect()
    try:
        conn.execute(text("CREATE TEMP TABLE idx_probe (id int, created_at text)"))
        conn.commit()
        for expr in (
            "(NULLIF(created_at,'')::timestamptz)",
            "(NULLIF(created_at,'')::timestamp)",
            "((NULLIF(created_at,'')::timestamp) AT TIME ZONE 'UTC')",
        ):
            with pytest.raises(Exception) as exc:
                conn.execute(text(f"CREATE INDEX ON idx_probe ({expr})"))
            assert "immutable" in str(exc.value).lower(), f"{expr} -> {exc.value}"
            conn.rollback()
    finally:
        conn.close()


def test_the_predicate_gives_the_same_answer_under_any_session_timezone(postgres_engine):
    """A stored timestamp must mean one instant, whatever zone the reader connected in.

    ``text::timestamptz`` resolves a value carrying NO offset using the session timezone.
    ``engine._pin_utc_session`` sets UTC on every engine this package builds, but these
    helpers take an arbitrary Connection, and psql, Alembic, or another service is not bound
    by that pin. Naive values are stored today: ``api/tokens.py:131`` writes
    ``'...T12:00:00'`` and ``autocm/gate/autonomy.py:107`` writes ``'... 12:00:00'``.

    Five rows, one instant, five spellings. Measured before the fix: under UTC the plain cast
    read all five as 12:00, and under Asia/Tokyo the two naive rows read as 03:00, nine hours
    out. Row 5 is why the helper is a CASE and not a plain ``::timestamp`` -- discarding a
    real offset would turn 05:00-07 into 05:00 UTC instead of 12:00.
    """
    conn = postgres_engine.connect()
    try:
        conn.execute(text("CREATE TEMP TABLE tz_probe (id int, ts text)"))
        conn.execute(text(
            "INSERT INTO tz_probe VALUES"
            " (1, '2026-08-29 12:00:00+00'),"     # func.now()
            " (2, '2026-08-29T12:00:00Z'),"       # _utc_now_iso
            " (3, '2026-08-29T12:00:00'),"        # api/tokens.py:131, naive
            " (4, '2026-08-29 12:00:00'),"        # autocm/gate/autonomy.py:107, naive
            " (5, '2026-08-29 05:00:00-07'),"     # a real non-UTC offset, same instant
            " (6, '  2026-08-29 12:00:00+00  ')"))  # padded: the offset test is end-anchored
        expr = ts_order("ts", "postgresql")
        answers = {}
        for zone in ("UTC", "Asia/Tokyo", "America/Los_Angeles"):
            conn.execute(text(f"SET TIME ZONE '{zone}'"))
            answers[zone] = [
                str(r[1]) for r in conn.execute(
                    text(f"SELECT id, ({expr} AT TIME ZONE 'UTC') FROM tz_probe ORDER BY id")
                ).fetchall()
            ]
        conn.execute(text("SET TIME ZONE 'UTC'"))

        for zone, got in answers.items():
            assert got == ["2026-08-29 12:00:00"] * 6, (
                f"under {zone} the six spellings of one instant disagreed: {got}")
        # A power check. All-equal is also satisfiable by an expression that returns a
        # constant, so pin that the value is the instant the rows actually encode.
        assert answers["UTC"][0] == "2026-08-29 12:00:00"
    finally:
        conn.close()
