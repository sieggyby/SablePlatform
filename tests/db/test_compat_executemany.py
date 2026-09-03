"""``CompatConnection.executemany`` — the method that took the weekly cycle down.

``get_db`` returns a ``CompatConnection`` and its docstring promises "existing code works
unchanged". It had no ``executemany``, so ``sable/vault/platform_sync.py`` raised
``AttributeError``. On PostgreSQL that aborted the surrounding transaction and the next
statement failed with ``InFailedSqlTransaction``, so one missing method failed three steps
of ``sable-weekly``.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from sable_platform.db.compat_conn import CompatConnection


@pytest.fixture()
def conn():
    engine = create_engine("sqlite://")
    with engine.connect() as sa_conn:
        sa_conn.execute(text("CREATE TABLE t (a INTEGER, b TEXT)"))
        yield CompatConnection(sa_conn)


def _rows(conn):
    return conn.execute("SELECT a, b FROM t ORDER BY a").fetchall()


def test_executemany_exists_at_all(conn):
    """The regression itself. Without the method this raises AttributeError."""
    assert hasattr(conn, "executemany")


def test_named_style_matches_the_real_caller(conn):
    """``platform_sync.py:529`` passes ``:named`` SQL and a list of dicts."""
    conn.executemany(
        "INSERT INTO t (a, b) VALUES (:a, :b)",
        [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}, {"a": 3, "b": "z"}],
    )
    assert [(r[0], r[1]) for r in _rows(conn)] == [(1, "x"), (2, "y"), (3, "z")]


def test_positional_style_converts_every_row(conn):
    """``?`` placeholders, one sequence per row. The values must not smear across rows."""
    conn.executemany("INSERT INTO t (a, b) VALUES (?, ?)", [(1, "x"), (2, "y")])
    assert [(r[0], r[1]) for r in _rows(conn)] == [(1, "x"), (2, "y")]


def test_an_empty_sequence_is_a_no_op(conn):
    """sqlite3 accepts an empty batch. SQLAlchemy raises StatementError on one.

    A caller that builds its batch from a query gets an empty list routinely, so the
    sqlite3 behaviour is the one that keeps ``get_db``'s promise true.
    """
    assert conn.executemany("INSERT INTO t (a, b) VALUES (:a, :b)", []) is None
    assert _rows(conn) == []


def test_the_empty_guard_is_load_bearing(conn):
    """POWER CHECK: prove raw SQLAlchemy really does raise, so the guard is not decoration."""
    with pytest.raises(Exception) as exc:
        conn._conn.execute(text("INSERT INTO t (a, b) VALUES (:a, :b)"), [])
    assert "bind parameter" in str(exc.value)


def test_accepts_a_text_construct(conn):
    """The caller may pass a ``text()`` object rather than a raw string."""
    conn.executemany(text("INSERT INTO t (a, b) VALUES (:a, :b)"), [{"a": 7, "b": "q"}])
    assert [(r[0], r[1]) for r in _rows(conn)] == [(7, "q")]


def test_a_generator_is_consumed_once(conn):
    """``seq_of_params`` may be a generator. Reading it twice would insert nothing."""
    conn.executemany(
        "INSERT INTO t (a, b) VALUES (:a, :b)",
        ({"a": i, "b": str(i)} for i in (1, 2)),
    )
    assert len(_rows(conn)) == 2
