"""Dialect-aware SQL expression helpers.

These helpers emit raw SQL fragments that work on both SQLite and PostgreSQL,
replacing SQLite-specific functions like ``julianday()`` and
``datetime('now', ...)``.

Each function takes a *dialect* string (``"sqlite"`` or ``"postgresql"``) so
callers can branch once at connection time and pass it through.
"""
from __future__ import annotations

from typing import Any

_SUPPORTED_DIALECTS = ("sqlite", "postgresql")


def get_dialect(conn: Any) -> str:
    """Extract the dialect name string from a connection object.

    Works with CompatConnection (has ``.dialect`` property returning the SA
    dialect object), native SA Connection, and raw ``sqlite3.Connection``
    (falls back to ``"sqlite"``).
    """
    dialect = getattr(conn, "dialect", None)
    if dialect is None:
        return "sqlite"
    # SA dialect objects have a .name attribute; CompatConnection.dialect
    # returns the SA dialect object directly.
    return getattr(dialect, "name", "sqlite")


def _check_dialect(dialect: str) -> None:
    if dialect not in _SUPPORTED_DIALECTS:
        raise ValueError(
            f"Unsupported dialect {dialect!r}; expected one of {_SUPPORTED_DIALECTS}"
        )


# ---------------------------------------------------------------------------
# Elapsed-time helpers (replace julianday arithmetic)
# ---------------------------------------------------------------------------

def hours_since(column: str, dialect: str) -> str:
    """SQL expression: hours elapsed since *column* value until now.

    Replaces ``(julianday('now') - julianday(col)) * 24``.
    """
    _check_dialect(dialect)
    if dialect == "sqlite":
        return f"(julianday('now') - julianday({column})) * 24"
    return f"EXTRACT(EPOCH FROM (NOW() - {column}::timestamptz)) / 3600.0"


def seconds_since(column: str, dialect: str) -> str:
    """SQL expression: seconds elapsed since *column* value until now.

    Replaces ``(julianday('now') - julianday(col)) * 86400``.
    """
    _check_dialect(dialect)
    if dialect == "sqlite":
        return f"(julianday('now') - julianday({column})) * 86400"
    return f"EXTRACT(EPOCH FROM (NOW() - {column}::timestamptz))"


def days_since(column: str, dialect: str) -> str:
    """SQL expression: fractional days elapsed since *column* value until now.

    Replaces ``julianday('now') - julianday(col)``.
    """
    _check_dialect(dialect)
    if dialect == "sqlite":
        return f"julianday('now') - julianday({column})"
    return f"EXTRACT(EPOCH FROM (NOW() - {column}::timestamptz)) / 86400.0"


def days_since_int(column: str, dialect: str) -> str:
    """SQL expression: integer days elapsed since *column* value until now.

    Replaces ``CAST(julianday('now') - julianday(col) AS INTEGER)``.
    """
    _check_dialect(dialect)
    if dialect == "sqlite":
        return f"CAST(julianday('now') - julianday({column}) AS INTEGER)"
    return f"CAST(EXTRACT(EPOCH FROM (NOW() - {column}::timestamptz)) / 86400.0 AS INTEGER)"


def days_between(col_a: str, col_b: str, dialect: str) -> str:
    """SQL expression: fractional days from *col_b* to *col_a*.

    Replaces ``julianday(col_a) - julianday(col_b)``.
    Result is positive when *col_a* is later than *col_b*.
    """
    _check_dialect(dialect)
    if dialect == "sqlite":
        return f"julianday({col_a}) - julianday({col_b})"
    return f"EXTRACT(EPOCH FROM ({col_a}::timestamptz - {col_b}::timestamptz)) / 86400.0"


def days_until(column: str, dialect: str) -> str:
    """SQL expression: fractional days from now until *column* value.

    Replaces ``julianday(col) - julianday('now')``.
    Result is positive when *column* is in the future.
    """
    _check_dialect(dialect)
    if dialect == "sqlite":
        return f"julianday({column}) - julianday('now')"
    return f"EXTRACT(EPOCH FROM ({column}::timestamptz - NOW())) / 86400.0"


# ---------------------------------------------------------------------------
# Timestamp offset helpers (replace datetime('now', offset))
# ---------------------------------------------------------------------------

def now_offset(offset: str, dialect: str) -> str:
    """SQL expression: current timestamp adjusted by a fixed *offset*.

    *offset* is a SQLite-style modifier string like ``'-90 days'`` or
    ``'-4 hours'``.  The function translates it for Postgres.

    Replaces ``datetime('now', '-90 days')``.
    """
    _check_dialect(dialect)
    if dialect == "sqlite":
        return f"datetime('now', '{offset}')"
    return f"(NOW() + INTERVAL '{offset}')"


def now_offset_param(param_name: str, dialect: str) -> str:
    """SQL expression: current timestamp offset by a bound parameter.

    The parameter should contain a SQLite-style modifier (e.g. ``'-4 hours'``).
    For Postgres, the parameter is cast to an ``INTERVAL``.

    Replaces ``datetime('now', :param)`` or ``datetime('now', ? || ' hours')``.
    """
    _check_dialect(dialect)
    if dialect == "sqlite":
        return f"datetime('now', :{param_name})"
    return f"(NOW() + (:{param_name})::interval)"
