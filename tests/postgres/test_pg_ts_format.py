"""Migration 090 against a live PostgreSQL, and what it closes.

DEFECTS_FOUND item 8. ``tests/db/test_ts_format.py`` covers the format itself. This file
covers the part that only a real server can answer: what the DEFAULT actually writes, what
the backfill actually converts, and whether a query that was wrong before is right after.
"""
from __future__ import annotations

import re

import pytest
from sqlalchemy import text

from sable_platform.db.ts_format import CANONICAL_REGEX

_CANON = re.compile(CANONICAL_REGEX)

# One instant in every spelling that can reach these columns, plus a later one to sort against.
_SPELLINGS = {
    "python_writer": "2026-07-30T12:00:00Z",
    "pg_now": "2026-07-30 12:00:00+00",
    "sqlite_now": "2026-07-30 12:00:00",
    "pg_now_fractional": "2026-07-30 12:00:00.123456+00",
    "isoformat": "2026-07-30T12:00:00+00:00",
    "other_offset": "2026-07-30 05:00:00-07:00",
    "trailing_space": "2026-07-30 12:00:00+00 ",
}


def test_no_text_column_still_defaults_to_bare_now(postgres_engine):
    """Read the SERVER, not ``schema.py``.

    ``schema.py`` and the migration chain can disagree after 60 revisions, and only the
    server knows which one the deployed database followed.
    """
    with postgres_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT table_name, column_name, column_default"
            "  FROM information_schema.columns"
            " WHERE table_schema = 'public'"
            "   AND data_type = 'text'"
            "   AND column_default IS NOT NULL"
            "   AND column_default LIKE '%now()%'"
            "   AND column_default NOT LIKE '%to_char%'"
        )).fetchall()
    assert not rows, f"{len(rows)} TEXT columns still default to bare now(): {rows[:5]}"


def test_the_migration_left_the_canonical_default_behind(postgres_engine):
    """The power check for the test above.

    A zero count means "fixed", "deleted" or "never there". This asserts the canonical
    default is PRESENT on a large number of columns, so an empty schema cannot read as a
    pass.
    """
    with postgres_engine.connect() as conn:
        count = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.columns"
            " WHERE table_schema = 'public' AND data_type = 'text'"
            "   AND column_default LIKE '%to_char%'"
        )).scalar()
    assert count >= 150, f"only {count} columns carry the canonical default"


@pytest.mark.parametrize("session_tz", ["UTC", "Asia/Tokyo", "America/Los_Angeles"])
def test_the_default_writes_the_canonical_spelling_under_any_session_zone(
    postgres_engine, session_tz
):
    """A naive render under Asia/Tokyo was measured nine hours out."""
    with postgres_engine.begin() as conn:
        conn.execute(text(f"SET TIME ZONE '{session_tz}'"))
        conn.execute(text("CREATE TEMP TABLE tz_probe (id TEXT PRIMARY KEY,"
                          " created_at TEXT NOT NULL DEFAULT"
                          " to_char(now() AT TIME ZONE 'UTC',"
                          " 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'))"))
        conn.execute(text("INSERT INTO tz_probe (id) VALUES ('a')"))
        written = conn.execute(text("SELECT created_at FROM tz_probe")).scalar()
        expected = conn.execute(text(
            "SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
        )).scalar()
    assert _CANON.match(written), f"not canonical under {session_tz}: {written!r}"
    assert written == expected, f"{session_tz} wrote {written!r}, UTC clock says {expected!r}"


def test_the_backfill_normalizes_every_spelling_to_one_string(postgres_engine):
    from sable_platform.db.ts_format import canonical_sql

    expr = canonical_sql("v", "postgresql")
    got = {}
    with postgres_engine.begin() as conn:
        for name, spelling in _SPELLINGS.items():
            got[name] = conn.execute(
                text(f"SELECT {expr} FROM (SELECT CAST(:v AS text) AS v) s"), {"v": spelling}
            ).scalar()
    assert set(got.values()) == {"2026-07-30T12:00:00Z"}, got


def test_the_backfill_is_idempotent(postgres_engine):
    from sable_platform.db.ts_format import canonical_sql

    expr = canonical_sql("v", "postgresql")
    with postgres_engine.begin() as conn:
        once = conn.execute(
            text(f"SELECT {expr} FROM (SELECT CAST(:v AS text) AS v) s"),
            {"v": "2026-07-30 12:00:00+00"},
        ).scalar()
        twice = conn.execute(
            text(f"SELECT {expr} FROM (SELECT CAST(:v AS text) AS v) s"), {"v": once}
        ).scalar()
    assert once == twice == "2026-07-30T12:00:00Z"


def test_a_boundary_day_comparison_is_wrong_mixed_and_right_canonical(postgres_engine):
    """The known-answer case, and the reason the format matters at all.

    A cutoff of ``'2026-07-30T00:00:00Z'`` and a row written by the PostgreSQL default at
    noon that day. As text the row sorts BELOW the cutoff, because space is ``0x20`` and
    ``T`` is ``0x54``, so a "since midnight" query drops it. Canonical, it does not.
    """
    cutoff = "2026-07-30T00:00:00Z"
    mixed = "2026-07-30 12:00:00+00"
    canonical = "2026-07-30T12:00:00Z"
    with postgres_engine.connect() as conn:
        wrong = conn.execute(text("SELECT CAST(:v AS text) >= CAST(:c AS text)"),
                             {"v": mixed, "c": cutoff}).scalar()
        right = conn.execute(text("SELECT CAST(:v AS text) >= CAST(:c AS text)"),
                             {"v": canonical, "c": cutoff}).scalar()
    assert wrong is False, "the mixed spelling no longer loses; this control is dead"
    assert right is True


def test_a_text_max_picks_the_wrong_row_mixed_and_the_right_one_canonical(postgres_engine):
    """The aggregate shape, which produces a plausible number off the wrong row."""
    with postgres_engine.begin() as conn:
        conn.execute(text("CREATE TEMP TABLE max_probe (v TEXT)"))
        conn.execute(text("INSERT INTO max_probe (v) VALUES ('2026-07-30 23:00:00+00'),"
                          " ('2026-07-30T09:00:00Z')"))
        mixed_max = conn.execute(text("SELECT MAX(v) FROM max_probe")).scalar()
        conn.execute(text("UPDATE max_probe SET v = '2026-07-30T23:00:00Z'"
                          " WHERE v = '2026-07-30 23:00:00+00'"))
        canonical_max = conn.execute(text("SELECT MAX(v) FROM max_probe")).scalar()
    assert mixed_max == "2026-07-30T09:00:00Z", "the mix no longer picks the wrong row"
    assert canonical_max == "2026-07-30T23:00:00Z"


# The 19 columns that USED to disagree: ``schema.py`` said ``Text`` and PostgreSQL stored
# ``timestamp with time zone``. Migration 091 converts them. Kept here so the test names
# what it is checking against rather than asserting an empty set with no reference point.
_FORMER_TYPE_DIVERGENCE = {
    ("api_tokens", "created_at"),
    ("api_tokens", "expires_at"),
    ("api_tokens", "last_used_at"),
    ("api_tokens", "revoked_at"),
    ("discord_burn_blocklist", "blocked_at"),
    ("discord_burn_optins", "opted_in_at"),
    ("discord_burn_random_log", "roasted_at"),
    ("discord_invite_snapshot", "captured_at"),
    ("discord_member_admit", "joined_at"),
    ("discord_message_observations", "captured_at"),
    ("discord_message_observations", "posted_at"),
    ("discord_peer_roast_flags", "flagged_at"),
    ("discord_peer_roast_tokens", "consumed_at"),
    ("discord_peer_roast_tokens", "granted_at"),
    ("discord_team_inviters", "added_at"),
    ("discord_user_observations", "computed_at"),
    ("discord_user_observations", "window_end"),
    ("discord_user_observations", "window_start"),
    ("discord_user_vibes", "inferred_at"),
}


def test_schema_py_and_postgresql_agree_on_every_declared_text_column(postgres_engine):
    """DEFECTS_FOUND item 9, closed by migration 091.

    ``schema.py`` opens with "This module is the single source of truth for the platform
    schema". It was wrong about 19 columns: it said ``Text`` and PostgreSQL stored
    ``timestamp with time zone``. Because this codebase reads through raw ``text()``
    queries, which bypass the SQLAlchemy type system, the same column handed back a ``str``
    on SQLite and a ``datetime`` on PostgreSQL.

    This reads the SERVER and compares it to ``schema.py``, so it fails whichever side
    drifts.
    """
    from sqlalchemy import Text

    from sable_platform.db import schema as sa_schema

    with postgres_engine.connect() as conn:
        actual = {(r[0], r[1]): r[2] for r in conn.execute(text(
            "SELECT table_name, column_name, data_type FROM information_schema.columns"
            " WHERE table_schema = 'public'"))}

    offenders = []
    declared_text = 0
    for table in sa_schema.metadata.sorted_tables:
        for col in table.columns:
            if not isinstance(col.type, Text):
                continue
            key = (table.name, col.name)
            if key not in actual:
                continue
            declared_text += 1
            if actual[key] != "text":
                offenders.append(f"{table.name}.{col.name} is {actual[key]}")
    assert not offenders, f"{len(offenders)} columns disagree with schema.py: {offenders[:5]}"
    # The power check. Zero offenders also describes a schema with no TEXT columns at all.
    assert declared_text > 500, f"only {declared_text} TEXT columns compared"


def test_every_column_migration_091_names_is_now_text(postgres_engine):
    """The presence half. The test above passes if the columns were DELETED."""
    with postgres_engine.connect() as conn:
        actual = {(r[0], r[1]): r[2] for r in conn.execute(text(
            "SELECT table_name, column_name, data_type FROM information_schema.columns"
            " WHERE table_schema = 'public'"))}
    missing = [f"{t}.{c}" for t, c in _FORMER_TYPE_DIVERGENCE if (t, c) not in actual]
    assert not missing, f"columns vanished rather than converted: {missing}"
    wrong = {f"{t}.{c}": actual[(t, c)] for t, c in _FORMER_TYPE_DIVERGENCE
             if actual[(t, c)] != "text"}
    assert not wrong, wrong


def _upgrade_to(database_url: str, revision: str) -> None:
    """``alembic upgrade <revision>``. Mirrors ``migrate_pg._run_alembic_upgrade``."""
    import importlib.resources
    import logging

    from alembic import command
    from alembic.config import Config

    alembic_root = importlib.resources.files("sable_platform.alembic")
    with importlib.resources.as_file(alembic_root) as script_dir:
        cfg = Config()
        cfg.set_main_option("script_location", str(script_dir))
        cfg.set_main_option("sqlalchemy.url", database_url)
        logging.getLogger("alembic").setLevel(logging.WARNING)
        command.upgrade(cfg, revision)


def test_migration_090_converts_real_rows_and_leaves_what_it_cannot_read(postgres_db_url):
    """End to end: seed the mix at 089, run 090, read the result.

    Every other test here exercises the EXPRESSION. This one exercises the migration, which
    is the thing that will run against production. It stops at revision 089 first, so the
    rows exist in the old spellings before 090 sees them.
    """
    from sqlalchemy import create_engine

    _upgrade_to(postgres_db_url, "a9b0c1d2e089")
    engine = create_engine(postgres_db_url)
    seeded = {
        "o_space": "2026-07-30 23:00:00+00",
        "o_space_frac": "2026-07-30 23:00:00.123456+00",
        "o_naive": "2026-07-30 23:00:00",
        "o_offset": "2026-07-30 16:00:00-07:00",
        "o_canonical": "2026-07-30T23:00:00Z",
        "o_junk": "not a timestamp at all",
    }
    with engine.begin() as conn:
        for org_id, value in seeded.items():
            conn.execute(
                text("INSERT INTO orgs (org_id, display_name, created_at, updated_at)"
                     " VALUES (:o, 'O', :v, :v)"),
                {"o": org_id, "v": value},
            )
    engine.dispose()

    _upgrade_to(postgres_db_url, "b0c1d2e3f090")

    engine = create_engine(postgres_db_url)
    with engine.connect() as conn:
        got = dict(conn.execute(text("SELECT org_id, created_at FROM orgs")).fetchall())
        default_written = conn.execute(text(
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_schema='public' AND table_name='orgs' AND column_name='created_at'"
        )).scalar()
    engine.dispose()

    for org_id in ("o_space", "o_space_frac", "o_naive", "o_offset", "o_canonical"):
        assert got[org_id] == "2026-07-30T23:00:00Z", f"{org_id} -> {got[org_id]!r}"
    # The guard: a value that is not timestamp-shaped is left exactly as it was.
    assert got["o_junk"] == seeded["o_junk"]
    assert "to_char" in (default_written or ""), default_written


def test_a_since_midnight_query_finds_the_row_it_used_to_drop(postgres_db_url):
    """The known-answer case for the whole migration, run against real rows.

    A cutoff of midnight and a row written at noon the same day by the old default. As text
    the row sorts BELOW the cutoff, because space is 0x20 and ``T`` is 0x54, so the query
    drops it. This asserts the answer BEFORE and AFTER, so a green result cannot come from
    the query being wrong in both trees.
    """
    from sqlalchemy import create_engine

    cutoff = "2026-07-30T00:00:00Z"
    sql = text("SELECT COUNT(*) FROM orgs WHERE org_id LIKE 'boundary%' AND created_at >= :c")

    _upgrade_to(postgres_db_url, "a9b0c1d2e089")
    engine = create_engine(postgres_db_url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO orgs (org_id, display_name, created_at, updated_at)"
                          " VALUES ('boundary1','O','2026-07-30 12:00:00+00',"
                          "         '2026-07-30 12:00:00+00')"))
        before = conn.execute(sql, {"c": cutoff}).scalar()
    engine.dispose()

    _upgrade_to(postgres_db_url, "b0c1d2e3f090")
    engine = create_engine(postgres_db_url)
    with engine.connect() as conn:
        after = conn.execute(sql, {"c": cutoff}).scalar()
    engine.dispose()

    assert before == 0, "the pre-migration query no longer drops the row; this control is dead"
    assert after == 1


def test_migration_090_survives_a_shape_matching_value_that_is_not_a_real_date(
    postgres_db_url, capsys
):
    """Codex round 5, MAJOR. `_PARSEABLE` is a shape and a shape is not a date.

    `'2026-13-01T00:00:00Z'` matches it, and `::timestamptz` on it raises "date/time field
    value out of range", which aborts the WHOLE migration. `pg_input_is_valid` asks the
    parser instead of guessing, so the row is skipped and counted rather than fatal.
    """
    from sqlalchemy import create_engine

    _upgrade_to(postgres_db_url, "a9b0c1d2e089")
    engine = create_engine(postgres_db_url)
    rows = {
        "o_bad_month": "2026-13-01T00:00:00Z",
        "o_bad_hour": "2026-07-30T25:00:00Z",
        "o_bad_day": "2026-02-30T00:00:00Z",
        "o_padded": " 2026-07-30T23:00:00Z ",
        "o_good": "2026-07-30 23:00:00+00",
    }
    with engine.begin() as conn:
        for org_id, value in rows.items():
            conn.execute(
                text("INSERT INTO orgs (org_id, display_name, created_at, updated_at)"
                     " VALUES (:o, 'O', :v, :v)"),
                {"o": org_id, "v": value},
            )
    engine.dispose()

    # The assertion is that this does not raise.
    _upgrade_to(postgres_db_url, "b0c1d2e3f090")

    engine = create_engine(postgres_db_url)
    with engine.connect() as conn:
        got = dict(conn.execute(text("SELECT org_id, created_at FROM orgs")).fetchall())
    engine.dispose()
    for org_id in ("o_bad_month", "o_bad_hour", "o_bad_day"):
        assert got[org_id] == rows[org_id], f"{org_id} was converted to {got[org_id]!r}"
    assert got["o_padded"] == "2026-07-30T23:00:00Z", f"padding kept: {got['o_padded']!r}"
    assert got["o_good"] == "2026-07-30T23:00:00Z"

    # Codex round 6 named this omission: the assertions above pass while the migration
    # reports "0 left unreadable" and the operator never learns the bad rows exist. All
    # three of them MATCH the canonical digit shape, so the not-canonical count cannot see
    # them. The report must name them separately.
    report = [ln for ln in capsys.readouterr().out.split("\n") if ln.startswith("[090]")]
    assert report, "migration 090 printed no report line"
    # SIX, not three. Each seeded row sets created_at AND updated_at, and the sweep counts
    # VALUES, not rows. My first version of this assertion said three and the test caught it.
    assert "6 canonical-shaped but not a real date" in report[-1], report[-1]
    assert "4 values rewritten" in report[-1], report[-1]


def test_the_pre_16_validity_fallback_answers_the_same_as_pg_input_is_valid(postgres_engine):
    """`pg_input_is_valid` arrived in PostgreSQL 16 and the deployment version is not pinned.

    The test server IS 16, so it takes the fast path and the fallback body would never run
    anywhere. This creates the function and drives it against the same values, so the branch
    that only fires on an older server is not shipped untested.
    """
    import importlib

    migration = importlib.import_module(
        "sable_platform.alembic.versions.b0c1d2e3f090_canonical_text_timestamps")

    cases = {
        "2026-07-30T23:00:00Z": True,
        "2026-07-30 23:00:00+00": True,
        "2026-07-30 23:00:00": True,
        "2026-13-01T00:00:00Z": False,
        "2026-07-30T25:00:00Z": False,
        "2026-02-30T00:00:00Z": False,
        "not a timestamp": False,
    }
    with postgres_engine.begin() as conn:
        conn.execute(text(migration.FALLBACK_FN_SQL))
        for value, expected in cases.items():
            fallback = conn.execute(
                text(f"SELECT {migration._FALLBACK_FN}(:v)"), {"v": value}).scalar()
            builtin = conn.execute(
                text("SELECT pg_input_is_valid(:v, 'timestamptz')"), {"v": value}).scalar()
            assert fallback == expected, f"fallback said {fallback} for {value!r}"
            assert fallback == builtin, (
                f"fallback and pg_input_is_valid disagree on {value!r}: "
                f"{fallback} vs {builtin}")


def test_migration_091_renders_the_same_instant_under_any_session_zone(postgres_db_url):
    """The known-answer case for the type conversion.

    A `timestamptz` holds an instant, and rendering it to text is where a session timezone
    can silently move it. The conversion goes through `AT TIME ZONE 'UTC'`, so it must give
    the same answer whatever the session is set to. Seeded at revision 090, where the column
    is still `timestamptz`, then converted.

    The seed is a whole second on purpose, so a failure here means the timezone moved and
    nothing else. Sub-second behaviour is a separate answer, in the next test.
    """
    from sqlalchemy import create_engine

    _upgrade_to(postgres_db_url, "b0c1d2e3f090")
    engine = create_engine(postgres_db_url)
    with engine.begin() as conn:
        conn.execute(text("SET TIME ZONE 'Asia/Tokyo'"))
        stored_type = conn.execute(text(
            "SELECT data_type FROM information_schema.columns"
            " WHERE table_name='api_tokens' AND column_name='created_at'")).scalar()
        assert stored_type == "timestamp with time zone", (
            f"the column is already {stored_type}; this test is not exercising a conversion")
        conn.execute(text(
            "INSERT INTO api_tokens (token_id, token_hash, label, operator_id, created_by,"
            " created_at, enabled, scopes_json, org_scopes_json)"
            " VALUES ('t1','h','l','op','cb', TIMESTAMPTZ '2026-07-30 23:00:00+00',"
            "         1, '[]', '[]')"))
    engine.dispose()

    _upgrade_to(postgres_db_url, "c1d2e3f4a091")

    engine = create_engine(postgres_db_url)
    with engine.connect() as conn:
        conn.execute(text("SET TIME ZONE 'America/Los_Angeles'"))
        got = conn.execute(text(
            "SELECT created_at FROM api_tokens WHERE token_id='t1'")).scalar()
        now_type = conn.execute(text(
            "SELECT data_type FROM information_schema.columns"
            " WHERE table_name='api_tokens' AND column_name='created_at'")).scalar()
    engine.dispose()
    assert now_type == "text", now_type
    assert isinstance(got, str), f"still not a str: {type(got).__name__}"
    assert got == "2026-07-30T23:00:00Z", got


def test_migration_091_truncates_sub_second_precision_rather_than_rounding(postgres_db_url):
    """The known-answer case for what the conversion does to a fractional second.

    `now()` gives microseconds, so every row the old `timestamptz` default wrote carries a
    fractional part. `to_char` truncates it. The migration says so, and this test pins the
    direction, because rounding and truncating differ by a whole second at `.999999` and
    rounding would roll a year at the last microsecond of December.

    Truncating is a choice, not a necessity. Fixed width is the necessity: PostgreSQL
    renders a fraction with trailing zeros stripped, so keeping what it renders gives a
    column several widths at once, and a variable width sorts wrong. A PADDED six-digit
    fraction would sort right and keep the microseconds. Migration 091 keeps whole seconds
    because that is the width the rest of the schema already runs at.
    """
    from sqlalchemy import create_engine

    _upgrade_to(postgres_db_url, "b0c1d2e3f090")
    engine = create_engine(postgres_db_url)
    seeded = {
        # token_id: (seeded timestamptz, expected text after conversion)
        "frac_max": ("2026-07-30 12:00:00.999999+00", "2026-07-30T12:00:00Z"),
        "frac_half": ("2026-07-30 12:00:00.500000+00", "2026-07-30T12:00:00Z"),
        "frac_min": ("2026-07-30 12:00:00.000001+00", "2026-07-30T12:00:00Z"),
        # Rounding here would roll the day, the month and the year at once.
        "year_end": ("2026-12-31 23:59:59.999999+00", "2026-12-31T23:59:59Z"),
        # Two instants 100 microseconds apart. They collapse to one string, by design.
        "collapse_a": ("2026-07-30 08:00:00.100000+00", "2026-07-30T08:00:00Z"),
        "collapse_b": ("2026-07-30 08:00:00.200000+00", "2026-07-30T08:00:00Z"),
    }
    with engine.begin() as conn:
        stored_type = conn.execute(text(
            "SELECT data_type FROM information_schema.columns"
            " WHERE table_name='api_tokens' AND column_name='created_at'")).scalar()
        assert stored_type == "timestamp with time zone", (
            f"the column is already {stored_type}; this test is not exercising a conversion")
        for token_id, (seed, _expected) in seeded.items():
            conn.execute(
                text(
                    "INSERT INTO api_tokens (token_id, token_hash, label, operator_id,"
                    " created_by, created_at, enabled, scopes_json, org_scopes_json)"
                    " VALUES (:tid, :tid, 'l', 'op', 'cb', CAST(:seed AS timestamptz),"
                    "         1, '[]', '[]')"
                ),
                {"tid": token_id, "seed": seed},
            )
        # The seeded microseconds are really there before the conversion. Without this the
        # test would pass against a server that never stored them in the first place.
        kept = conn.execute(text(
            "SELECT count(*) FROM api_tokens"
            " WHERE date_part('microseconds', created_at)::int % 1000000 <> 0")).scalar()
        assert kept == len(seeded), f"only {kept} of {len(seeded)} rows kept a fraction"
    engine.dispose()

    _upgrade_to(postgres_db_url, "c1d2e3f4a091")

    engine = create_engine(postgres_db_url)
    with engine.connect() as conn:
        rows = dict(conn.execute(text(
            "SELECT token_id, created_at FROM api_tokens")).fetchall())
        ordered = [r[0] for r in conn.execute(text(
            "SELECT token_id FROM api_tokens"
            " ORDER BY created_at ASC, token_id ASC")).fetchall()]
    engine.dispose()

    for token_id, (_seed, expected) in seeded.items():
        assert rows[token_id] == expected, f"{token_id}: {rows[token_id]!r}"
    # The collapse is the accepted consequence, so state it as an expectation.
    assert rows["collapse_a"] == rows["collapse_b"], "expected these two to collapse"
    # And the tiebreaker still gives one total order over the collapsed pair.
    assert ordered.index("collapse_a") < ordered.index("collapse_b"), ordered


def test_token_validation_no_longer_raises_on_postgresql(postgres_conn):
    """DEFECTS_FOUND item 9, the CRITICAL half, end to end.

    Before migration 091 `expires_at` came back as a `datetime` on PostgreSQL and
    `validate_token` raised for every token that carried an expiry. The suite never saw it
    because the suite runs on SQLite. This drives the real function against a real
    PostgreSQL.
    """
    import datetime as _dt

    from sable_platform.api.tokens import issue_token, verify_token

    mint = dict(operator_id="op", created_by="cb", org_scopes=["org1"],
                scopes=["read_only"])
    # A CompatConnection, because `verify_token` reads `row["enabled"]` and a bare
    # SQLAlchemy row is a tuple. `issue_token` commits for itself, so no outer transaction.
    conn = postgres_conn
    live_id, live_raw = issue_token(conn, label="live", expires_in_days=7, **mint)
    gone_id, gone_raw = issue_token(conn, label="expired", expires_in_days=7, **mint)
    past = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    conn.execute(text("UPDATE api_tokens SET expires_at = :e WHERE token_id = :t"),
                 {"e": past, "t": gone_id})
    conn.commit()

    # Pre-091 this RAISED rather than returning either answer. Measured against
    # PostgreSQL 16: '<=' not supported between instances of 'datetime.datetime' and 'str'.
    assert verify_token(conn, live_raw) is not None, "a live token was rejected"
    assert verify_token(conn, gone_raw) is None, "an expired token was accepted"
    assert live_id and gone_id
