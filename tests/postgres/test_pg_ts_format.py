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


# The 12 columns ``schema.py`` declares as ``Text`` that PostgreSQL actually stores as
# ``timestamp with time zone``. Measured against a database built by the migration chain.
_TYPE_DIVERGENCE = {
    ("api_tokens", "created_at"),
    ("discord_burn_blocklist", "blocked_at"),
    ("discord_burn_optins", "opted_in_at"),
    ("discord_burn_random_log", "roasted_at"),
    ("discord_invite_snapshot", "captured_at"),
    ("discord_member_admit", "joined_at"),
    ("discord_message_observations", "captured_at"),
    ("discord_peer_roast_flags", "flagged_at"),
    ("discord_peer_roast_tokens", "granted_at"),
    ("discord_team_inviters", "added_at"),
    ("discord_user_observations", "computed_at"),
    ("discord_user_vibes", "inferred_at"),
}


def test_the_schema_py_type_divergence_is_exactly_these_twelve_columns(postgres_engine):
    """A pin, not a pass. DEFECTS_FOUND item 9.

    ``schema.py`` opens with "This module is the single source of truth for the platform
    schema". For these twelve columns that is false: it says ``Text`` and PostgreSQL says
    ``timestamp with time zone``. A raw ``text()`` read therefore returns ``datetime`` on
    PostgreSQL and ``str`` on SQLite for the same column, which is a dual-dialect divergence
    in the RETURN VALUE.

    Migration 090 skips them deliberately: a real timestamp type needs no canonical spelling,
    and ``SET DEFAULT to_char(...)`` on one is a type error that aborts the migration. This
    test exists so the set cannot grow without someone seeing it.
    """
    with postgres_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND data_type <> 'text'"
            "   AND column_default IS NOT NULL AND column_default LIKE '%now()%'"
        )).fetchall()
    found = {(r[0], r[1]) for r in rows}
    assert found == _TYPE_DIVERGENCE, (
        f"the divergence moved. new: {sorted(found - _TYPE_DIVERGENCE)}, "
        f"gone: {sorted(_TYPE_DIVERGENCE - found)}")


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
