"""Tests for sable_platform.db.compat dialect-aware SQL helpers."""
from __future__ import annotations

import pytest

from sable_platform.db import compat

DIALECTS = ("sqlite", "postgresql")

HELPERS = [
    (compat.hours_since, ("col",)),
    (compat.seconds_since, ("col",)),
    (compat.days_since, ("col",)),
    (compat.days_since_int, ("col",)),
    (compat.days_between, ("col_a", "col_b")),
    (compat.days_until, ("col",)),
    (compat.now_offset, ("'-90 days'",)),
    (compat.now_offset_param, ("cutoff",)),
    (compat.json_extract_text, ("detail_json", "guild_id")),
    (compat.date_of_iso_text, ("timestamp",)),
]


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("func,args", HELPERS, ids=[h[0].__name__ for h in HELPERS])
def test_helper_returns_nonempty_string(func, args, dialect):
    """Every helper returns a non-empty SQL fragment for each dialect."""
    result = func(*args, dialect=dialect)
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.parametrize("dialect", DIALECTS)
def test_hours_since_contains_column_name(dialect):
    result = compat.hours_since("my_col", dialect)
    assert "my_col" in result


@pytest.mark.parametrize("dialect", DIALECTS)
def test_days_between_contains_both_columns(dialect):
    result = compat.days_between("a", "b", dialect)
    assert "a" in result
    assert "b" in result


def test_sqlite_hours_since_matches_legacy():
    """SQLite output must match the exact julianday pattern used in production code."""
    result = compat.hours_since("completed_at", "sqlite")
    assert result == "(julianday('now') - julianday(completed_at)) * 24"


def test_sqlite_days_since_int_matches_legacy():
    result = compat.days_since_int("d.completed_at", "sqlite")
    assert result == "CAST(julianday('now') - julianday(d.completed_at) AS INTEGER)"


def test_sqlite_now_offset_matches_legacy():
    result = compat.now_offset("-90 days", "sqlite")
    assert result == "datetime('now', '-90 days')"


def test_postgres_hours_since_uses_extract():
    result = compat.hours_since("col", "postgresql")
    assert "EXTRACT" in result
    assert "3600" in result


def test_postgres_now_offset_uses_interval():
    result = compat.now_offset("-4 hours", "postgresql")
    assert "INTERVAL" in result


def test_unsupported_dialect_raises():
    """Passing an unrecognized dialect should fail fast."""
    with pytest.raises(ValueError, match="Unsupported dialect"):
        compat.hours_since("col", "mysql")


# ---------------------------------------------------------------------------
# json_extract_text + date_of_iso_text (R0 of the /roast plan)
# ---------------------------------------------------------------------------


def test_json_extract_text_sqlite_uses_json_extract():
    result = compat.json_extract_text("detail_json", "guild_id", "sqlite")
    assert result == "json_extract(detail_json, '$.guild_id')"


def test_json_extract_text_postgres_uses_jsonb_arrow():
    result = compat.json_extract_text("detail_json", "guild_id", "postgresql")
    assert result == "(detail_json::jsonb)->>'guild_id'"


def test_json_extract_text_rejects_path_injection():
    with pytest.raises(ValueError, match="plain SQL identifier"):
        compat.json_extract_text("detail_json", "x'); DROP TABLE", "sqlite")
    with pytest.raises(ValueError, match="plain SQL identifier"):
        compat.json_extract_text("detail_json", 'foo"bar', "postgresql")
    # Nested-path attempts (legal-looking but break Postgres semantics) rejected.
    with pytest.raises(ValueError, match="plain SQL identifier"):
        compat.json_extract_text("detail_json", "foo.bar", "sqlite")


def test_json_extract_text_rejects_column_injection():
    """The column slot must also reject non-identifier strings — the guard
    must not be one-sided. R0 QA finding #2."""
    with pytest.raises(ValueError, match="plain SQL identifier"):
        compat.json_extract_text(
            "detail_json) UNION ALL SELECT password FROM users WHERE ('1'='1",
            "guild_id",
            "sqlite",
        )
    with pytest.raises(ValueError, match="plain SQL identifier"):
        compat.json_extract_text("a; DROP TABLE x", "k", "postgresql")


def test_date_of_iso_text_rejects_column_injection():
    with pytest.raises(ValueError, match="plain SQL identifier"):
        compat.date_of_iso_text("timestamp) OR 1=1 --", "sqlite")


def test_date_of_iso_text_sqlite_uses_substr():
    result = compat.date_of_iso_text("timestamp", "sqlite")
    assert result == "date(substr(timestamp, 1, 19))"


def test_date_of_iso_text_postgres_uses_cast():
    result = compat.date_of_iso_text("timestamp", "postgresql")
    assert result == "(timestamp::timestamp)::date"


# ---------------------------------------------------------------------------
# Bind-parameter guard on elapsed-time helpers (rounds 2 and 3)
# ---------------------------------------------------------------------------

GUARDED_HELPERS = [
    compat.days_since,
    compat.days_until,
    compat.days_since_int,
    compat.seconds_since,
    compat.hours_since,
]

# The PostgreSQL expectations carry NULLIF: one row holding '' makes
# ``''::timestamptz`` raise and takes the whole query down, which is the same class of
# silent failure these helpers exist to prevent. The SQLite expectations are unchanged,
# because julianday('') already returns NULL there.
# The PostgreSQL branch of every helper reads a stored TEXT timestamp through one shared
# expression, so these cases name it once instead of pasting it five times. Its exact shape
# is pinned by test_pg_instant_shape below, and its BEHAVIOUR -- one answer under any session
# timezone -- by tests/postgres/test_pg_gc_timestamp_formats.py.
_PG_INSTANT = compat._pg_instant("run_date")

COLUMN_CASES = [
    (
        compat.days_since,
        "julianday('now') - julianday(run_date)",
        "EXTRACT(EPOCH FROM (NOW() - " + _PG_INSTANT + ")) / 86400.0",
    ),
    (
        compat.days_until,
        "julianday(run_date) - julianday('now')",
        "EXTRACT(EPOCH FROM (" + _PG_INSTANT + " - NOW())) / 86400.0",
    ),
    (
        compat.days_since_int,
        "CAST(julianday('now') - julianday(run_date) AS INTEGER)",
        "CAST(EXTRACT(EPOCH FROM (NOW() - " + _PG_INSTANT + ")) / 86400.0 AS INTEGER)",
    ),
    (
        compat.seconds_since,
        "(julianday('now') - julianday(run_date)) * 86400",
        "EXTRACT(EPOCH FROM (NOW() - " + _PG_INSTANT + "))",
    ),
    (
        compat.hours_since,
        "(julianday('now') - julianday(run_date)) * 24",
        "EXTRACT(EPOCH FROM (NOW() - " + _PG_INSTANT + ")) / 3600.0",
    ),
]


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize(
    "func", GUARDED_HELPERS, ids=[f.__name__ for f in GUARDED_HELPERS]
)
def test_elapsed_helpers_reject_bind_parameter(func, dialect):
    """A bind parameter never binds here: on Postgres the emitted ``::`` cast
    collides with the ``:name`` parameter syntax and the query fails silently
    at runtime."""
    with pytest.raises(ValueError, match="bind parameter"):
        func(":ts", dialect)


@pytest.mark.parametrize(
    "func", GUARDED_HELPERS, ids=[f.__name__ for f in GUARDED_HELPERS]
)
def test_reject_message_names_argument_and_points_to_python(func):
    with pytest.raises(ValueError) as excinfo:
        func(":ts", "postgresql")
    assert "':ts'" in str(excinfo.value)
    assert "Python" in str(excinfo.value)


@pytest.mark.parametrize(
    "func", GUARDED_HELPERS, ids=[f.__name__ for f in GUARDED_HELPERS]
)
def test_reject_checks_stripped_form(func):
    with pytest.raises(ValueError, match="bind parameter"):
        func("  :ts ", "sqlite")


@pytest.mark.parametrize(
    "func,sqlite_sql,postgres_sql",
    COLUMN_CASES,
    ids=[c[0].__name__ for c in COLUMN_CASES],
)
def test_plain_column_sql_unchanged(func, sqlite_sql, postgres_sql):
    assert func("run_date", "sqlite") == sqlite_sql
    assert func("run_date", "postgresql") == postgres_sql


def test_pg_instant_shape():
    """The one expression every PostgreSQL branch reads a TEXT timestamp through.

    A plain ``::timestamptz`` resolves an offset-less value using the SESSION timezone, and
    naive values are stored today (``api/tokens.py:131``, ``autocm/gate/autonomy.py:107``).
    A plain ``::timestamp`` would DISCARD a real offset instead. Hence the CASE: measured on
    live PostgreSQL 16 in
    tests/postgres/test_pg_gc_timestamp_formats.py::test_the_predicate_gives_the_same_answer_under_any_session_timezone.
    """
    got = compat._pg_instant("started_at")
    assert got == (
        "(CASE WHEN NULLIF(started_at, '') ~ '(Z|[+-][0-9]{2}(:?[0-9]{2})?)$'"
        " THEN NULLIF(started_at, '')::timestamptz"
        " ELSE (NULLIF(started_at, '')::timestamp AT TIME ZONE 'UTC') END)"
    )


def test_ts_column_casts_on_postgres_and_never_on_sqlite():
    """Every timestamp column in schema.py is TEXT, so comparing one against
    now_offset_param() raises `operator does not exist: text > timestamp with time zone`
    on PostgreSQL. ts_column casts it. SQLite must NEVER see ::timestamptz."""
    from sable_platform.db.compat import ts_column

    assert ts_column("started_at", "postgresql") == compat._pg_instant("started_at")
    assert "::timestamptz" in ts_column("started_at", "postgresql")
    assert ts_column("started_at", "sqlite") == "NULLIF(started_at, '')"
    assert "timestamptz" not in ts_column("started_at", "sqlite")
    # the loop-B guard still applies: a bind parameter is not a column
    with pytest.raises(ValueError):
        ts_column(":cutoff", "postgresql")
    with pytest.raises(ValueError):
        ts_column(":cutoff", "sqlite")
