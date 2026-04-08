"""Compatibility connection wrapper for the sqlite3 → SQLAlchemy transition.

``CompatConnection`` wraps a SQLAlchemy :class:`Connection` so that existing
code using ``?``-style positional parameters and ``row["col"]`` dict access
continues to work unchanged.  New code can pass :func:`sqlalchemy.text`
objects with ``:named`` parameters through the same interface.

This is a transitional layer — once all modules are converted to native
SQLAlchemy ``text()`` calls, this module can be removed.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


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


def _positional_to_named(sql: str, params: list | tuple) -> tuple[str, dict[str, Any]]:
    """Convert ``?``-style positional placeholders to ``:_p0``, ``:_p1``, etc.

    Returns ``(converted_sql, named_params_dict)``.
    """
    parts = sql.split("?")
    if len(parts) - 1 != len(params):
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
