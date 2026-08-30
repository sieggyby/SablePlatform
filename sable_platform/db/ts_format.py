"""One canonical spelling for the TEXT timestamp columns.

The platform stores its timestamps as TEXT. That is survivable only while every writer
agrees on the spelling, and until this module existed they did not: a
``server_default=func.now()`` renders ``now()`` on PostgreSQL and ``CURRENT_TIMESTAMP`` on
SQLite, both of which produce a SPACE separator, while 67 Python call sites render
``strftime("%Y-%m-%dT%H:%M:%SZ")`` with a ``T``. Space is ``0x20`` and ``T`` is ``0x54``,
so every comparison, ``ORDER BY``, ``MIN`` and ``MAX`` over one of those columns was decided
by the separator character before it reached the clock.

``sable_platform.db.compat`` fixes that at READ time, one call site at a time. This module
fixes it at WRITE time, once, for every column: the canonical spelling is what 67 call sites
already produce, and the server defaults now produce it too.

**The canonical spelling is fixed width on purpose.** Lexicographic order equals
chronological order only while every value has the same shape. A VARIABLE-width fractional
part breaks it: ``'...T12:00:00.5Z'`` sorts BELOW ``'...T12:00:00Z'`` because ``.`` is
``0x2E`` and ``Z`` is ``0x5A``. PostgreSQL renders a ``timestamptz`` to text with trailing
zeros REMOVED, so a column that keeps what it renders holds several widths at once:
measured, ``.500000`` gives ``.5`` and ``.000000`` gives no fraction at all.

Fixed width is the requirement, and whole seconds is one width that meets it. A six-digit
fraction is another: ``to_char(..., 'SS.US')`` pads it on PostgreSQL. This module picks
whole seconds because SQLite cannot match that width without string surgery. Its
``strftime('%f')`` renders exactly three digits, padded, never six.

Native ``timestamptz`` columns would be better still, and this module does not get there. It
makes the column uniform; it does not change the stored TYPE. Reads keep returning ``str`` on
both dialects, which is why this can ship without touching the hundreds of callers that a
type change would break.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Text
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import FunctionElement

# The canonical spelling: UTC, second precision, ``T`` separator, ``Z`` suffix.
ISO_Z_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
CANONICAL_LEN = 20

# A value already in canonical form. Used to make the backfill idempotent and, later, as the
# body of a CHECK constraint if the operator decides to enforce the format in the database.
CANONICAL_REGEX = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"

# Every spelling the backfill knows how to convert. A value outside this set is LEFT ALONE
# and counted, rather than fed to a cast that would abort the migration.
PARSEABLE_REGEX = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(\.[0-9]+)?(Z|[+-][0-9]{2}(:?[0-9]{2})?)?$"
)

# ``now()`` in canonical form. ``AT TIME ZONE 'UTC'`` first, so the session's TimeZone cannot
# reach the output: measured, a naive render under Asia/Tokyo is nine hours out.
PG_NOW_CANONICAL = "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
SQLITE_NOW_CANONICAL = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"


def utc_now_iso() -> str:
    """The current instant in the canonical spelling."""
    return datetime.now(timezone.utc).strftime(ISO_Z_FORMAT)


class utc_now_iso_sql(FunctionElement):  # noqa: N801  (a SQL construct, not a class name)
    """``server_default`` that writes the canonical spelling on either dialect.

    Replaces ``func.now()``. ``func.now()`` was never wrong on its own; it was wrong beside
    a Python writer that spelled the same instant differently.
    """

    type = Text()
    name = "utc_now_iso_sql"
    inherit_cache = True


@compiles(utc_now_iso_sql, "postgresql")
def _compile_pg(element, compiler, **kw):  # noqa: ARG001
    return PG_NOW_CANONICAL


@compiles(utc_now_iso_sql, "sqlite")
def _compile_sqlite(element, compiler, **kw):  # noqa: ARG001
    return SQLITE_NOW_CANONICAL


@compiles(utc_now_iso_sql)
def _compile_default(element, compiler, **kw):  # noqa: ARG001
    """Any other dialect keeps the old behaviour rather than emitting invalid SQL."""
    return "CURRENT_TIMESTAMP"


def canonical_sql(column: str, dialect: str) -> str:
    """SQL that rewrites *column* into the canonical spelling.

    PostgreSQL reads the value through the same branch as
    :func:`sable_platform.db.compat._pg_instant`: a value carrying an offset is a
    ``timestamptz`` already, a naive one is UTC. Reading it any other way makes the answer
    depend on the session's TimeZone.
    """
    if dialect == "sqlite":
        return f"strftime('{ISO_Z_FORMAT}', NULLIF({column}, ''))"
    trimmed = f"TRIM(NULLIF({column}, ''))"
    instant = (
        f"(CASE WHEN {trimmed} ~ '(Z|[+-][0-9]{{2}}(:?[0-9]{{2}})?)$'"
        f" THEN {trimmed}::timestamptz"
        f" ELSE ({trimmed}::timestamp AT TIME ZONE 'UTC') END)"
    )
    return f"to_char({instant} AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"


def now_canonical_sql(dialect: str) -> str:
    """SQL for "now", in the canonical spelling, for *dialect*.

    Use this in place of a bare ``CURRENT_TIMESTAMP`` in an ``UPDATE`` or ``INSERT``.
    ``CURRENT_TIMESTAMP`` writes ``'2026-08-29 12:00:00'`` on SQLite and
    ``'2026-08-29 12:00:00.123456+00'`` on PostgreSQL. Both carry a SPACE, so a row written
    by one of them and a row written by a Python ``...T...Z`` writer cannot be compared,
    ordered, or aggregated as text.
    """
    if dialect not in ("sqlite", "postgresql"):
        raise ValueError(f"dialect must be sqlite or postgresql, got {dialect!r}")
    return SQLITE_NOW_CANONICAL if dialect == "sqlite" else PG_NOW_CANONICAL
