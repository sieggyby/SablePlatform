"""``CompatConnection.executemany`` — the method that took the weekly cycle down.

``get_db`` returns a ``CompatConnection`` and its docstring promises "existing code works
unchanged". It had no ``executemany``, so ``sable/vault/platform_sync.py`` raised
``AttributeError``. On PostgreSQL that aborted the surrounding transaction and the next
statement failed with ``InFailedSqlTransaction``, so one missing method failed three steps
of ``sable-weekly``.
"""
from __future__ import annotations

import collections
import sqlite3

import pytest
from sqlalchemy import bindparam, create_engine, text

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


def test_an_empty_batch_returns_a_cursor_like_result(conn):
    """sqlite3 accepts an empty batch and still hands back a cursor.

    A caller that builds its batch from a query gets an empty list routinely, so the
    sqlite3 behaviour is the one that keeps ``get_db``'s promise true. The first version
    returned ``None``, which raised ``AttributeError`` for a caller that read
    ``.rowcount`` off the return value.
    """
    result = conn.executemany("INSERT INTO t (a, b) VALUES (:a, :b)", [])
    assert result.rowcount == 0
    assert result.lastrowid is None
    assert result.fetchone() is None
    assert result.fetchall() == []
    assert list(result) == []
    assert _rows(conn) == []


def test_the_empty_batch_contract_is_sqlite3s_own():
    """KNOWN ANSWER: pin the contract to what sqlite3 does, not to what I wrote."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (a INTEGER, b TEXT)")
    cursor = db.executemany("INSERT INTO t (a, b) VALUES (?, ?)", [])
    assert cursor.rowcount == 0
    assert cursor.lastrowid is None


def test_a_string_row_is_positional_just_like_sqlite3(conn):
    """A ``str`` is a sequence, and sqlite3 spreads it across the placeholders.

    Measured: ``executemany("... VALUES (?,?)", ["xy"])`` inserts ``('x', 'y')``. The
    first version tested for ``list`` or ``tuple`` only, so a string row fell through to
    the named path and raised ``ArgumentError``.
    """
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (a INTEGER, b TEXT)")
    db.executemany("INSERT INTO t (a, b) VALUES (?, ?)", ["xy", "za"])
    expected = db.execute("SELECT a, b FROM t ORDER BY a").fetchall()
    assert expected == [("x", "y"), ("z", "a")]

    conn.executemany("INSERT INTO t (a, b) VALUES (?, ?)", ["xy", "za"])
    assert [(r[0], r[1]) for r in _rows(conn)] == expected


def test_a_mixed_batch_is_rejected(conn):
    """Silently accepting a mixed batch writes rows nothing checked.

    An int-keyed dict reads as positional data to SQLAlchemy, so
    ``[(1, "ok"), {0: 2, 1: "bad"}]`` went in whole. sqlite3 raises on that input.
    """
    with pytest.raises(ValueError, match="mixed batch"):
        conn.executemany(
            "INSERT INTO t (a, b) VALUES (?, ?)", [(1, "ok"), {0: 2, 1: "bad"}]
        )
    assert _rows(conn) == []


def test_a_mixed_batch_is_rejected_the_other_way_round(conn):
    """Named row first, positional row second. Order must not decide the answer."""
    with pytest.raises(ValueError, match="mixed batch"):
        conn.executemany(
            "INSERT INTO t (a, b) VALUES (:a, :b)", [{"a": 1, "b": "ok"}, (2, "bad")]
        )
    assert _rows(conn) == []


def test_real_sqlite3_also_rejects_the_mixed_batch():
    """KNOWN ANSWER: the rejection is sqlite3's behaviour, not an invention of mine."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (a INTEGER, b TEXT)")
    with pytest.raises(sqlite3.ProgrammingError):
        db.executemany(
            "INSERT INTO t (a, b) VALUES (?, ?)", [(1, "ok"), {0: 2, 1: "bad"}]
        )


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


def test_a_namedtuple_row_is_positional(conn):
    """A namedtuple is a tuple, so it must keep working as positional data."""
    Row = collections.namedtuple("Row", "a b")
    conn.executemany("INSERT INTO t (a, b) VALUES (?, ?)", [Row(1, "x"), Row(2, "y")])
    assert [(r[0], r[1]) for r in _rows(conn)] == [(1, "x"), (2, "y")]


@pytest.mark.parametrize(
    "row", [{1, 2}, 5, None, 3.5], ids=["set", "int", "None", "float"]
)
def test_a_row_that_can_carry_no_parameters_is_named_and_rejected(conn, row):
    """"Not a Mapping" was too loose. A set passed it and blew up deeper down.

    ``_positional_to_named`` then raised ``TypeError: 'set' object is not subscriptable``,
    naming neither the row nor its index. sqlite3 rejects these too, so only the error
    message changes.
    """
    with pytest.raises(TypeError, match=r"row 0 is a \w+"):
        conn.executemany("INSERT INTO t (a, b) VALUES (?, ?)", [row])
    assert _rows(conn) == []


def test_the_offending_row_is_named_by_index(conn):
    """A bad row late in a batch must say WHICH row, not just that something is wrong."""
    with pytest.raises(TypeError, match="row 2 is a set"):
        conn.executemany(
            "INSERT INTO t (a, b) VALUES (?, ?)", [(1, "x"), (2, "y"), {3, "z"}]
        )
    assert _rows(conn) == []


def test_named_sql_with_sequence_rows_is_rejected_like_sqlite3(conn):
    """An audit called this "sqlite3-valid". It is not. Measured, sqlite3 rejects it too.

    ``ProgrammingError: Binding 1 (':a') is a named parameter, but you supplied a sequence
    which requires nameless (qmark) placeholders.`` Teaching the wrapper to ACCEPT it would
    make it looser than the library it stands in for.
    """
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (a INTEGER, b TEXT)")
    with pytest.raises(sqlite3.ProgrammingError, match="named parameter"):
        db.executemany("INSERT INTO t (a,b) VALUES (:a,:b)", [(1, "x")])

    with pytest.raises(ValueError, match="no . placeholders but 2 positional parameter"):
        conn.executemany("INSERT INTO t (a, b) VALUES (:a, :b)", [(1, "x")])
    assert _rows(conn) == []


def test_the_error_says_what_is_wrong_not_just_that_a_count_is_off(conn):
    """The old message blamed the placeholder count, which is the symptom.

    "SQL has 0 ? placeholders but 2 params were given" does not tell you that the SQL is
    :named and the rows are sequences. A real count mismatch still reports the count.
    """
    with pytest.raises(ValueError, match="Parameter count mismatch"):
        conn.executemany("INSERT INTO t (a, b) VALUES (?, ?)", [(1, "x", "extra")])


def test_a_question_mark_inside_a_string_literal_is_a_known_limitation(conn):
    """DIVERGENCE from sqlite3, documented rather than fixed. No caller hits it.

    ``_positional_to_named`` splits the SQL on ``?`` with no awareness of string literals,
    so ``VALUES (?, '?')`` counts two placeholders where sqlite3 counts one. sqlite3
    accepts the statement and inserts a literal question mark.

    This is NOT new and NOT specific to ``executemany``: ``execute`` shares the splitter
    and behaves the same way. Fixing it means making the splitter literal-aware, which
    changes every ``?`` call site in the codebase. A sweep found ZERO SQL strings in
    ``sable_platform/`` with a ``?`` inside a literal, so the fix carries more risk than
    the defect. This test pins the current behaviour so the divergence is discoverable.
    """
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (a INTEGER, b TEXT)")
    db.executemany("INSERT INTO t (a,b) VALUES (?, '?')", [(1,)])
    assert db.execute("SELECT a, b FROM t").fetchall() == [(1, "?")]

    with pytest.raises(ValueError, match="Parameter count mismatch"):
        conn.executemany("INSERT INTO t (a, b) VALUES (?, '?')", [(1,)])
    with pytest.raises(ValueError, match="Parameter count mismatch"):
        conn.execute("INSERT INTO t (a, b) VALUES (?, '?')", (1,))


def test_the_bind_style_message_does_not_guess_when_the_colon_is_a_literal(conn):
    """The regex matches a colon inside a string literal, so the wording must survive that.

    ``VALUES (1, ' :x')`` has no bind at all, but ``(?<![:\\w\\\\]):(\\w+)(?!:)`` matches the
    space-preceded colon. Naming it a ":named placeholder" outright would be a guess. The
    message states what was measured, that the SQL has no ``?`` placeholders, and offers the
    named-parameter case as a possibility.

    Casts, time literals and JSON paths do NOT match: measured, ``a::text``, ``'12:00'`` and
    ``j->>'a:b'`` all miss, because the colon follows a word character.
    """
    for sql in ("INSERT INTO t (a, b) VALUES (1, ' :x')", "INSERT INTO t (a, b) VALUES (:a, :b)"):
        with pytest.raises(ValueError, match=r"no \? placeholders but 1 positional parameter"):
            conn.executemany(sql, [(1,)])
    assert _rows(conn) == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT a::text FROM t",
        "SELECT * FROM t WHERE b = '12:00'",
        "SELECT j->>'a:b' FROM t",
        "INSERT INTO t (a, b) VALUES (1, 2)",
    ],
    ids=["cast", "time-literal", "json-path", "no-colon"],
)
def test_the_named_bind_regex_does_not_misfire_on_ordinary_sql(sql):
    """A false match would turn a plain count mismatch into a misleading message."""
    from sable_platform.db.compat_conn import _NAMED_PLACEHOLDER

    assert not _NAMED_PLACEHOLDER.search(sql)


def test_a_text_construct_is_passed_through_not_rebuilt(conn):
    """A mutation sweep found this hole: rebuilding the construct kills its bindparams.

    ``executemany`` passes a non-``str`` statement THROUGH unchanged. Replacing that with
    ``text(str(sql))`` produces the same SQL string, so every existing test still passed,
    but ``_bindparams`` differs, measured. A construct carrying a TYPED bindparam loses that
    type on the round trip.

    The type has to actually TRANSFORM the value, or the test cannot fail. My first attempt
    used ``Integer`` against an INTEGER column, and SQLite coerces ``"7"`` to ``7`` through
    column affinity whether the bindparam survives or not. A ``TypeDecorator`` that changes
    the value leaves no such escape.
    """
    from sqlalchemy import String
    from sqlalchemy.types import TypeDecorator

    class Shouty(TypeDecorator):
        """Uppercases on the way in. Nothing in SQLite does this by itself."""

        impl = String
        cache_ok = True

        def process_bind_param(self, value, dialect):
            return None if value is None else value.upper()

    statement = text("INSERT INTO t (a, b) VALUES (:a, :b)").bindparams(
        bindparam("b", type_=Shouty())
    )
    conn.executemany(statement, [{"a": 1, "b": "quiet"}, {"a": 2, "b": "also quiet"}])

    assert [(r[0], r[1]) for r in _rows(conn)] == [(1, "QUIET"), (2, "ALSO QUIET")]


def test_the_exact_production_call_that_broke_sable_weekly(conn):
    """The literal shape from ``sable/vault/platform_sync.py``, read off the production host.

    Step 8 of ``vault_sync`` builds a list of dicts and calls ``executemany`` with ``:named``
    SQL::

        conn.executemany(
            \"\"\"INSERT INTO artifacts (org_id, job_id, artifact_type, path,
                                        metadata_json, stale)
               VALUES (:org_id, :job_id, :artifact_type, :path, :metadata_json, :stale)\"\"\",
            [{...} for r in new_artifact_rows],
        )

    On 2026-08-31 that raised ``'CompatConnection' object has no attribute 'executemany'``,
    which aborted the PostgreSQL transaction so the next step failed with
    ``InFailedSqlTransaction``. One missing method, two failed steps, two orgs.

    ``new_artifact_rows`` is built from a query and comes back EMPTY routinely, which is why
    the empty case is half this test rather than an afterthought.
    """
    conn.execute(
        "CREATE TABLE artifacts (org_id TEXT, job_id TEXT, artifact_type TEXT,"
        " path TEXT, metadata_json TEXT, stale INTEGER)"
    )
    sql = (
        "INSERT INTO artifacts (org_id, job_id, artifact_type, path, metadata_json, stale)"
        " VALUES (:org_id, :job_id, :artifact_type, :path, :metadata_json, :stale)"
    )

    def row(n):
        return {"org_id": "tig", "job_id": f"j{n}", "artifact_type": "report",
                "path": f"/p/{n}", "metadata_json": "{}", "stale": 0}

    result = conn.executemany(sql, [row(1), row(2)])
    assert result.rowcount == 2
    stored = conn.execute("SELECT job_id, org_id FROM artifacts ORDER BY job_id").fetchall()
    assert [(r["job_id"], r["org_id"]) for r in stored] == [("j1", "tig"), ("j2", "tig")]

    # The empty batch, which is the routine case and used to raise StatementError.
    empty = conn.executemany(sql, [])
    assert empty.rowcount == 0
    assert len(conn.execute("SELECT job_id FROM artifacts").fetchall()) == 2
