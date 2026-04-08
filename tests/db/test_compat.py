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
