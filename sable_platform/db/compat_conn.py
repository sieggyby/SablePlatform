"""Compatibility connection wrapper for the sqlite3 → SQLAlchemy transition.

``CompatConnection`` wraps a SQLAlchemy :class:`Connection` so that existing
code using ``?``-style positional parameters and ``row["col"]`` dict access
continues to work unchanged.  New code can pass :func:`sqlalchemy.text`
objects with ``:named`` parameters through the same interface.

This is a transitional layer — once all modules are converted to native
SQLAlchemy ``text()`` calls, this module can be removed.
"""
from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

# A ":name" bind, using SQLAlchemy's own rule: a colon NOT preceded by a word character.
_NAMED_PLACEHOLDER = re.compile(r"(?<![:\w\\]):(\w+)(?!:)")


class CompatRow:
    """Row wrapper supporting both positional (``row[0]``) and dict
    (``row["col"]``) access — matching :class:`sqlite3.Row` behaviour.
    """

    __slots__ = ("_row", "_mapping")

    def __init__(self, sa_row):
        self._row = sa_row
        self._mapping = sa_row._mapping

    # --- access patterns -----------------------------------------------

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._row[key]
        return self._mapping[key]

    def __contains__(self, key) -> bool:
        return key in self._mapping

    def __len__(self) -> int:
        return len(self._mapping)

    def __iter__(self) -> Iterator[str]:
        return iter(self._mapping)

    # --- mapping protocol (makes ``dict(row)`` work) -------------------

    def keys(self):
        return self._mapping.keys()

    def values(self):
        return self._mapping.values()

    def items(self):
        return self._mapping.items()


class CompatResult:
    """Cursor-result wrapper that returns :class:`CompatRow` objects.

    Also exposes ``.mappings()`` so already-converted SA-style code works too.
    """

    __slots__ = ("_result",)

    def __init__(self, sa_result):
        self._result = sa_result

    @property
    def lastrowid(self):
        return self._result.lastrowid

    @property
    def rowcount(self):
        return self._result.rowcount

    def fetchone(self) -> CompatRow | None:
        row = self._result.fetchone()
        return CompatRow(row) if row is not None else None

    def fetchall(self) -> list[CompatRow]:
        return [CompatRow(r) for r in self._result.fetchall()]

    def mappings(self):
        """Pass through to the underlying SA result for native SA callers."""
        return self._result.mappings()

    def __iter__(self):
        for row in self._result:
            yield CompatRow(row)


class _EmptyBatchResult:
    """What :meth:`CompatConnection.executemany` returns for an EMPTY batch.

    sqlite3 returns a real cursor for an empty batch, measured: ``rowcount`` is ``0``
    and ``lastrowid`` is ``None``. SQLAlchemy cannot supply one, because it raises
    ``StatementError`` on an empty parameter list instead of executing anything. This
    object stands in, so a caller that reads ``.rowcount`` off the return value keeps
    working. Returning ``None`` there raised ``AttributeError`` instead.
    """

    __slots__ = ()

    lastrowid = None
    rowcount = 0

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def mappings(self):
        return iter(())

    def __iter__(self):
        return iter(())


class CompatConnection:
    """SQLAlchemy :class:`Connection` with sqlite3-compatible execute API.

    Accepts both ``?``-positional and ``:named`` parameter styles.
    """

    def __init__(self, sa_conn: Connection) -> None:
        self._conn = sa_conn

    # Expose dialect so compat helpers can branch on it.
    @property
    def dialect(self):
        return self._conn.dialect

    def execute(self, sql, params=None) -> CompatResult:
        """Execute *sql* and return a :class:`CompatResult`.

        *sql* can be:
        - A plain string with ``?`` positional params and a tuple/list.
        - A plain string with ``:named`` params and a dict.
        - A :func:`sqlalchemy.text` object with a dict.
        """
        if isinstance(sql, str):
            if params is not None and isinstance(params, (list, tuple)):
                sa_sql, sa_params = _positional_to_named(sql, params)
                return CompatResult(self._conn.execute(text(sa_sql), sa_params))
            elif params is not None and isinstance(params, dict):
                return CompatResult(self._conn.execute(text(sql), params))
            else:
                return CompatResult(self._conn.execute(text(sql)))
        # Already a text() or other SA construct.
        if params is not None:
            return CompatResult(self._conn.execute(sql, params))
        return CompatResult(self._conn.execute(sql))

    def executemany(self, sql, seq_of_params):
        """Execute *sql* once per parameter set, the sqlite3 way.

        :func:`sable_platform.db.connection.get_db` promises that "existing code works
        unchanged". ``executemany`` was the one method missing from that promise, and
        ``sable/vault/platform_sync.py`` calls it. On PostgreSQL the ``AttributeError``
        aborted the surrounding transaction, so the next statement failed too:

            (psycopg2.errors.InFailedSqlTransaction) current transaction is aborted

        That took the weekly automation cycle down with it.

        Accepts the same two parameter styles as :meth:`execute`: ``?``-positional with
        a sequence per row, or ``:named`` with a dict per row.
        """
        rows = list(seq_of_params)
        if not rows:
            # sqlite3 treats an empty sequence as a no-op and still hands back a cursor.
            # SQLAlchemy raises StatementError, "A value is required for bind parameter",
            # measured. A caller that builds its batch from a query gets an empty list
            # routinely, so matching sqlite3 here is what keeps the promise above true.
            return _EmptyBatchResult()

        # One parameter style for the whole batch, and every row a type that can carry
        # parameters at all. sqlite3 raises ProgrammingError on a mixed batch, measured:
        # "Binding 1 has no name, but you supplied a dictionary". Accepting one silently
        # is worse than failing, because an int-keyed dict reads as positional data to
        # SQLAlchemy and the row goes in unchecked.
        first_style = _row_style(rows[0], 0)
        for i, row in enumerate(rows[1:], start=1):
            if _row_style(row, i) != first_style:
                raise ValueError(
                    f"executemany got a mixed batch: row 0 is {first_style} but row {i} "
                    f"is not. Use one parameter style for every row."
                )

        if isinstance(sql, str) and first_style == "positional":
            # Positional. sqlite3 accepts any SEQUENCE as a row, ``str`` and ``bytes``
            # included: measured, ``executemany("... VALUES (?,?)", ["xy"])`` inserts
            # ``('x', 'y')``. Testing for list/tuple alone rejected that. Every row
            # shares one placeholder count, so the converted SQL is the same for all of
            # them and only the values differ.
            sa_sql, first = _positional_to_named(sql, rows[0])
            named = [first]
            named.extend(_positional_to_named(sql, r)[1] for r in rows[1:])
            return CompatResult(self._conn.execute(text(sa_sql), named))

        statement = text(sql) if isinstance(sql, str) else sql
        return CompatResult(self._conn.execute(statement, rows))

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def begin(self):
        """Start an explicit transaction (returns SA NestedTransaction)."""
        return self._conn.begin()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        return False


def _row_style(row: Any, index: int) -> str:
    """Classify one ``executemany`` row as ``"named"`` or ``"positional"``.

    A row must be a Mapping or a Sequence. "Anything that is not a Mapping" was too
    loose: a ``set`` passed it, then ``_positional_to_named`` raised
    ``TypeError: 'set' object is not subscriptable`` from deep inside the conversion,
    naming neither the row nor its index. sqlite3 rejects a set too, so the answer is
    the same and only the error is better.
    """
    if isinstance(row, Mapping):
        return "named"
    if isinstance(row, Sequence):
        # ``str`` and ``bytes`` are Sequences, and that is deliberate: sqlite3 spreads
        # them across the placeholders rather than rejecting them.
        return "positional"
    raise TypeError(
        f"executemany row {index} is a {type(row).__name__}, which is neither a mapping "
        f"(:named) nor a sequence (?-positional)."
    )


def _positional_to_named(sql: str, params: list | tuple) -> tuple[str, dict[str, Any]]:
    """Convert ``?``-style positional placeholders to ``:_p0``, ``:_p1``, etc.

    Returns ``(converted_sql, named_params_dict)``.
    """
    parts = sql.split("?")
    if len(parts) - 1 != len(params):
        if len(parts) == 1 and _NAMED_PLACEHOLDER.search(sql):
            # No "?" at all, and the SQL carries something shaped like a ":name" bind while
            # the caller passed a sequence. sqlite3 rejects this too, and names the cause:
            # "Binding 1 (':a') is a named parameter, but you supplied a sequence which
            # requires nameless (qmark) placeholders." A bare count mismatch does not say
            # that, and the count is the symptom rather than the problem.
            #
            # The wording stays true even when the colon is NOT a bind. A colon inside a
            # string literal, ``VALUES (1, ' :x')``, matches the regex, and the advice below
            # is still the right advice: the SQL has no ? placeholders, so a sequence cannot
            # bind to it either way. Claiming ":named placeholders" outright would be a
            # guess; this states what was measured.
            raise ValueError(
                f"SQL has no ? placeholders but {len(params)} positional parameter(s) were "
                "given. If the SQL uses :named placeholders, pass a mapping per row."
            )
        raise ValueError(
            f"Parameter count mismatch: SQL has {len(parts) - 1} ? placeholders "
            f"but {len(params)} params were given"
        )
    named: dict[str, Any] = {}
    result = parts[0]
    for i, part in enumerate(parts[1:]):
        pname = f"_p{i}"
        named[pname] = params[i]
        result += f":{pname}" + part
    return result, named
