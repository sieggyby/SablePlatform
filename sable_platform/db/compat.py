"""Dialect-aware SQL expression helpers.

These helpers emit raw SQL fragments that work on both SQLite and PostgreSQL,
replacing SQLite-specific functions like ``julianday()`` and
``datetime('now', ...)``.

Most functions take a *dialect* string (``"sqlite"`` or ``"postgresql"``) so
callers can branch once at connection time and pass it through.
``get_dialect`` is the one helper that takes a connection and returns the
dialect string for it.
"""
from __future__ import annotations

import re
from typing import Any

_SUPPORTED_DIALECTS = ("sqlite", "postgresql")

# Plain SQL identifier — first char letter/underscore, rest alnum/underscore.
# Used to guard column + key params in helpers that string-interpolate into
# SQL. Anything else gets rejected to keep injection from leaking through a
# future caller that forgets the helper is unsafe by default.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


_COLREF_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def _check_column_ref(value: str, kind: str = "column") -> None:
    """A plain or table-qualified column name, and nothing else.

    ``ts_column`` interpolates its argument into SQL with an f-string, so rejecting bind
    parameters (``_check_column``) is not a strong enough guard for a future caller.
    ``journey.py`` legitimately passes ``t.expires_at``, so one dotted qualifier is allowed.
    """
    if not isinstance(value, str) or not _COLREF_RE.match(value):
        raise ValueError(f"{kind} must be a plain or table-qualified column, got {value!r}")


def _check_identifier(value: str, kind: str) -> None:
    if not isinstance(value, str) or not _IDENT_RE.fullmatch(value):
        raise ValueError(f"{kind} must be a plain SQL identifier, got {value!r}")


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


def _check_column(column: str) -> None:
    # These helpers string-interpolate *column* into raw SQL, so a bind
    # parameter can never be substituted by the driver: on PostgreSQL the
    # emitted ``::timestamptz`` cast collides with the ``:name`` parameter
    # syntax and the query fails at runtime, silently disabling the alert.
    if isinstance(column, str) and column.strip().startswith(":"):
        raise ValueError(
            f"{column!r} is a bind parameter, not a column name; compute "
            "the value in Python instead"
        )


# A stored TEXT timestamp, read as an INSTANT, without asking the session what zone it is in.
#
# `text::timestamptz` resolves a value that carries no offset using the SESSION timezone.
# `engine._pin_utc_session` sets UTC on every engine this package builds, but these helpers
# take an arbitrary Connection and psql, a migration tool, or another service is not bound by
# that. Naive values are stored today: `api/tokens.py:131` writes '...T12:00:00' and
# `autocm/gate/autonomy.py:107` writes '... 12:00:00'.
#
# Measured on live PostgreSQL 16, five rows all recording 2026-08-29 12:00 UTC in five
# spellings. Under UTC the plain cast reads all five as 12:00. Under Asia/Tokyo the two naive
# rows read as 03:00, nine hours out. The expression below reads all five as 12:00 under both.
#
# A value that carries an offset keeps it; `::timestamp` would DISCARD it and turn
# '2026-08-29 05:00:00-07' into 05:00 UTC rather than 12:00. That is why this is a CASE and
# not a single cast.
_PG_TS_OFFSET_RE = r"'(Z|[+-][0-9]{2}(:?[0-9]{2})?)$'"


def _pg_instant(column: str) -> str:
    """PostgreSQL: *column* as a ``timestamptz``, the same answer under any session zone.

    ``TRIM`` first. The offset test is anchored to end-of-string, so a single trailing space
    sends a value that DOES carry an offset down the naive branch and shifts it by the
    session's UTC offset. Trimming is free and removes a silent wrong answer; PostgreSQL's
    own cast tolerates the whitespace either way.
    """
    inner = f"TRIM(NULLIF({column}, ''))"
    return (f"(CASE WHEN {inner} ~ {_PG_TS_OFFSET_RE} THEN {inner}::timestamptz"
            f" ELSE ({inner}::timestamp AT TIME ZONE 'UTC') END)")


# ---------------------------------------------------------------------------
# Elapsed-time helpers (replace julianday arithmetic)
# ---------------------------------------------------------------------------

def hours_since(column: str, dialect: str) -> str:
    """SQL expression: hours elapsed since *column* value until now.

    Replaces ``(julianday('now') - julianday(col)) * 24``.
    """
    _check_dialect(dialect)
    _check_column(column)
    if dialect == "sqlite":
        return f"(julianday('now') - julianday({column})) * 24"
    return f"EXTRACT(EPOCH FROM (NOW() - {_pg_instant(column)})) / 3600.0"


def seconds_since(column: str, dialect: str) -> str:
    """SQL expression: seconds elapsed since *column* value until now.

    Replaces ``(julianday('now') - julianday(col)) * 86400``.
    """
    _check_dialect(dialect)
    _check_column(column)
    if dialect == "sqlite":
        return f"(julianday('now') - julianday({column})) * 86400"
    return f"EXTRACT(EPOCH FROM (NOW() - {_pg_instant(column)}))"


def days_since(column: str, dialect: str) -> str:
    """SQL expression: fractional days elapsed since *column* value until now.

    Replaces ``julianday('now') - julianday(col)``.
    """
    _check_dialect(dialect)
    _check_column(column)
    if dialect == "sqlite":
        return f"julianday('now') - julianday({column})"
    return f"EXTRACT(EPOCH FROM (NOW() - {_pg_instant(column)})) / 86400.0"


def days_since_int(column: str, dialect: str) -> str:
    """SQL expression: integer days elapsed since *column* value until now.

    Replaces ``CAST(julianday('now') - julianday(col) AS INTEGER)``.
    """
    _check_dialect(dialect)
    _check_column(column)
    if dialect == "sqlite":
        return f"CAST(julianday('now') - julianday({column}) AS INTEGER)"
    return f"CAST(EXTRACT(EPOCH FROM (NOW() - {_pg_instant(column)})) / 86400.0 AS INTEGER)"


def days_between(col_a: str, col_b: str, dialect: str) -> str:
    """SQL expression: fractional days from *col_b* to *col_a*.

    Replaces ``julianday(col_a) - julianday(col_b)``.
    Result is positive when *col_a* is later than *col_b*.
    """
    _check_dialect(dialect)
    if dialect == "sqlite":
        return f"julianday({col_a}) - julianday({col_b})"
    return (f"EXTRACT(EPOCH FROM ({_pg_instant(col_a)}"
            f" - {_pg_instant(col_b)})) / 86400.0")


def days_until(column: str, dialect: str) -> str:
    """SQL expression: fractional days from now until *column* value.

    Replaces ``julianday(col) - julianday('now')``.
    Result is positive when *column* is in the future.
    """
    _check_dialect(dialect)
    _check_column(column)
    if dialect == "sqlite":
        return f"julianday({column}) - julianday('now')"
    return f"EXTRACT(EPOCH FROM ({_pg_instant(column)} - NOW())) / 86400.0"


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


def ts_column(column: str, dialect: str) -> str:
    """A TEXT timestamp *column*, made comparable to a real timestamp.

    EVERY timestamp column in ``schema.py`` is declared ``Text``. PostgreSQL has no
    ``text > timestamp with time zone`` operator, so comparing one against
    :func:`now_offset_param` raises, and the check it guards never fires. Cast it, exactly
    as :func:`hours_since` and :func:`seconds_since` already do.

    SQLite compares text lexicographically and must NOT receive ``::timestamptz``, which is
    not valid there.

    Empty strings are excluded with ``NULLIF``. Verified against a live PostgreSQL 16: a
    SINGLE row holding ``''`` raises ``invalid input syntax for type timestamp with time
    zone`` and takes the whole query down, which is the very symptom this function exists to
    remove. ``NULLIF`` is applied on BOTH dialects so they agree: without it SQLite sorts
    ``''`` below every real timestamp and reports such a row as arbitrarily old.

    A non-empty but unparseable value still raises. That is deliberate. It is a data-integrity
    fault and should be loud, not silently treated as NULL.
    """
    _check_dialect(dialect)
    _check_column_ref(column)
    if dialect == "sqlite":
        return f"NULLIF({column}, '')"
    return f"{_pg_instant(column)}"


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


# ---------------------------------------------------------------------------
# JSON + ISO-timestamp helpers (replace SQLite json_extract / date(substr(...)))
# ---------------------------------------------------------------------------

def json_extract_text(column: str, key: str, dialect: str) -> str:
    """SQL expression: extract a top-level JSON field as text.

    Replaces ``json_extract(column, '$.key')`` which is SQLite-only.

    The column is expected to hold JSON-encoded text (Sable's ``detail_json``
    convention — TEXT columns carrying serialized JSON). ``key`` is the
    top-level field name (no nested paths; we don't need them yet, and keeping
    the helper simple avoids the JSON-path escaping rabbit-hole).

    Both ``column`` and ``key`` are validated as plain SQL identifiers so a
    future caller can't accidentally smuggle SQL through either slot.
    """
    _check_dialect(dialect)
    _check_identifier(column, "json column")
    _check_identifier(key, "json key")
    if dialect == "sqlite":
        return f"json_extract({column}, '$.{key}')"
    return f"({column}::jsonb)->>'{key}'"


def date_of_iso_text(column: str, dialect: str) -> str:
    """SQL expression: extract the calendar date portion from an ISO-8601 text column.

    Replaces ``date(substr(column, 1, 19))`` — used to compare audit-log
    timestamps to a ``YYYY-MM-DD`` day bucket. The substr trims a possible
    timezone suffix on SQLite; on Postgres we cast to ``timestamp`` and then
    to ``date`` directly.

    ``column`` is validated as a plain SQL identifier so callers can't
    accidentally inject through it.
    """
    _check_dialect(dialect)
    _check_identifier(column, "timestamp column")
    if dialect == "sqlite":
        return f"date(substr({column}, 1, 19))"
    return f"({column}::timestamp)::date"


# ---------------------------------------------------------------------------
# Whole-comparison helpers (both operands, so the two sides cannot disagree)
# ---------------------------------------------------------------------------

def ts_before(column: str, param_name: str, dialect: str) -> str:
    """SQL predicate: TEXT timestamp *column* is strictly earlier than now plus an offset.

    ``ts_column`` makes the LEFT side comparable and leaves the right side to the caller.
    That split is the bug this helper closes. A caller that pairs ``ts_column`` with a
    Python-formatted cutoff string gets a TEXT comparison on SQLite, and a TEXT comparison
    is decided by the separator character before it ever reaches the clock.

    Measured on SQLite. Rows at ``'2026-07-30 12:00:00'`` and ``'2026-07-30T12:00:00Z'`` are
    the same instant, four hours AFTER a ``'2026-07-30T08:00:00Z'`` cutoff, so both must
    survive. Space (0x20) sorts below ``T`` (0x54), so the space-form row compares below the
    cutoff and is deleted. Reformatting the cutoff to space-form only swaps the victim: the
    ``T`` row then sorts above every cutoff and is never deleted. ``julianday`` on both sides
    returns the right answer for both formats.

    The mixed-format case is not hypothetical. ``relay_tweets.fetched_at`` is written by
    ``relay/db.py:576`` through the ``func.now()`` server default and by ``relay/db.py:3172``
    through ``_utc_now_iso()``, so that one column holds both spellings.

    *param_name* binds a SQLite-style offset modifier such as ``'-30 days'``, exactly as
    :func:`now_offset_param` expects. Pass the offset, never a formatted timestamp.

    ``NULLIF`` keeps an empty string out of the comparison on both dialects: on PostgreSQL a
    single ``''`` raises and takes the whole query down. A NULL comparison is NULL, so an
    empty value is neither before nor after the cutoff. Decide that case explicitly at the
    call site when it matters; :func:`stuck_run_predicate` is the worked example.

    SQLite limit, measured: ``julianday`` parses ``'...T08:00:00Z'``, ``'...+00:00'``, and a
    fractional part, but returns NULL for a two-digit offset like ``'+00'``. PostgreSQL
    writes exactly that spelling. A SQLite file never holds a PostgreSQL-written value, so
    this costs nothing today. It is a real edge and it is written down rather than assumed.
    """
    _check_dialect(dialect)
    _check_column_ref(column)
    _check_identifier(param_name, "parameter name")
    if dialect == "sqlite":
        return f"julianday(NULLIF({column}, '')) < julianday('now', :{param_name})"
    return f"{_pg_instant(column)} < (NOW() + (:{param_name})::interval)"


def ts_at_or_after(column: str, param_name: str, dialect: str) -> str:
    """SQL predicate: TEXT timestamp *column* is at or after now plus an offset.

    The mirror of :func:`ts_before`, for a retention window expressed as "still inside".
    ``gc_orphan_chats`` needs it: the chat survives while one message is newer than the
    cutoff, so the TEXT-comparison error there deletes a chat that a live message still
    references rather than merely deleting a message early.
    """
    _check_dialect(dialect)
    _check_column_ref(column)
    _check_identifier(param_name, "parameter name")
    if dialect == "sqlite":
        return f"julianday(NULLIF({column}, '')) >= julianday('now', :{param_name})"
    return f"{_pg_instant(column)} >= (NOW() + (:{param_name})::interval)"


def stuck_run_predicate(column: str, param_name: str, dialect: str) -> str:
    """SQL predicate: a run is stuck, counting a missing start time as stuck.

    Four sites ask the same question of ``workflow_runs`` and two of them used to answer it
    differently. ``mark_timed_out_runs`` and ``_check_stuck_runs`` carried the
    ``NULLIF(...) IS NULL`` disjunct; ``dashboard_cmds`` and ``workflow_cmds`` did not, so an
    empty ``started_at`` was reported as stuck by the alert path and hidden by both operator
    views. One helper, so the four cannot drift again.

    An empty or NULL ``started_at`` on a RUNNING row is a data fault, and treating it as
    healthy is the dangerous direction: ``idx_workflow_runs_active_lock`` still counts the
    row as active, so the workflow it belongs to stays blocked with nothing reporting it.
    """
    _check_dialect(dialect)
    _check_column_ref(column)
    return (f"(NULLIF({column}, '') IS NULL"
            f" OR {ts_before(column, param_name, dialect)})")


_TS_OPS = ("<", "<=", ">", ">=", "=")


def ts_compare(column: str, op: str, param_name: str, dialect: str) -> str:
    """SQL predicate: TEXT timestamp *column* compared to an ABSOLUTE timestamp parameter.

    :func:`ts_before` and :func:`ts_at_or_after` cover a window expressed as now-plus-offset.
    This covers the other shape, where the caller already has a specific instant: a cache
    TTL, a digest week boundary, a suppression cutoff computed in Python.

    Same contract, same reason. A TEXT comparison against a formatted string is decided by
    the separator character before it reaches the clock, and the two spellings that reach
    these columns differ in exactly that character.

    Bind the parameter as an ISO-8601 string. On PostgreSQL the cast is written
    ``CAST(:name AS timestamptz)`` and NOT ``:name::timestamptz``: the driver reads the
    second colon pair of ``:name::type`` as another bind and the statement fails to parse.
    """
    _check_dialect(dialect)
    _check_column_ref(column)
    _check_identifier(param_name, "parameter name")
    if op not in _TS_OPS:
        raise ValueError(f"operator must be one of {_TS_OPS}, got {op!r}")
    if dialect == "sqlite":
        return f"julianday(NULLIF({column}, '')) {op} julianday(:{param_name})"
    return (f"{_pg_instant(column)} {op}"
            f" CAST(:{param_name} AS timestamptz)")


def ts_order(column: str, dialect: str) -> str:
    """SQL expression: a TEXT timestamp *column* as a value that SORTS chronologically.

    ``ORDER BY`` and ``MIN`` on a mixed-spelling TEXT column are wrong for the same reason a
    comparison is: ``'2026-07-30 12:00'`` sorts below ``'2026-07-30T09:00'`` because the
    separator is read before the hour. A digest that orders a week of messages this way
    interleaves them, and ``MIN`` returns a row that is not the earliest.
    """
    _check_dialect(dialect)
    _check_column_ref(column)
    if dialect == "sqlite":
        return f"julianday(NULLIF({column}, ''))"
    return f"{_pg_instant(column)}"


def ts_max(column: str, dialect: str) -> str:
    """SQL expression: the LATEST instant in a group, from a TEXT timestamp *column*.

    ``MAX(col)`` on TEXT returns the lexicographic maximum, which is not the latest row once
    two spellings share the column: every ``'...T...'`` value outranks every ``'... ...'``
    value whatever the clock says. Casting the result afterwards converts the WRONG row, so a
    ``days_since_int("MAX(completed_at)", ...)`` reads a real number off the wrong timestamp
    and reports a freshness that never existed.

    Returns a timestamptz on PostgreSQL and a Julian day number on SQLite, so this is for
    arithmetic and ordering, not for display. Use :func:`days_since_int_of_max` when the
    caller wants an age.
    """
    _check_dialect(dialect)
    _check_column_ref(column)
    if dialect == "sqlite":
        return f"MAX(julianday(NULLIF({column}, '')))"
    return f"MAX({_pg_instant(column)})"


def days_since_int_of_max(column: str, dialect: str) -> str:
    """SQL expression: whole days since the LATEST instant in a group.

    The aggregate form of :func:`days_since_int`. Pairing that helper with ``MAX(col)``
    picks the wrong row first and then casts it correctly, which is the harder failure to
    notice: the answer is a plausible number rather than an error.
    """
    _check_dialect(dialect)
    _check_column_ref(column)
    if dialect == "sqlite":
        return f"CAST(julianday('now') - {ts_max(column, dialect)} AS INTEGER)"
    return (f"CAST(EXTRACT(EPOCH FROM (NOW() - {ts_max(column, dialect)}))"
            f" / 86400.0 AS INTEGER)")
