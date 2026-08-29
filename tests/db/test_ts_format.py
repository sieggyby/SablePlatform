"""The canonical TEXT timestamp spelling, and what it buys.

DEFECTS_FOUND item 8. The read-side helpers in ``db/compat.py`` fix one call site at a
time. These tests are about the other end: once every writer spells a timestamp the same
way, a plain text comparison is a correct instant comparison and the 191 remaining call
sites stop being wrong.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, MetaData, Table, Text, create_engine, text

from sable_platform.db import schema as sa_schema
from sable_platform.db.ts_format import (
    CANONICAL_REGEX,
    ISO_Z_FORMAT,
    canonical_sql,
    utc_now_iso,
    utc_now_iso_sql,
)

_CANON = re.compile(CANONICAL_REGEX)

# One instant, in every spelling that can reach these columns.
_INSTANT = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
_SPELLINGS = [
    "2026-07-30T12:00:00Z",             # the Python writers
    "2026-07-30 12:00:00+00",           # PostgreSQL now()
    "2026-07-30 12:00:00",              # SQLite CURRENT_TIMESTAMP
    "2026-07-30 12:00:00.123456+00",    # PostgreSQL now() with its fraction
    "2026-07-30T12:00:00+00:00",        # datetime.isoformat()
    "2026-07-30 05:00:00-07:00",        # the same instant, written elsewhere
]


def test_the_python_writer_emits_the_canonical_spelling():
    assert _CANON.match(utc_now_iso()), utc_now_iso()
    assert len(utc_now_iso()) == 20


def test_lexicographic_order_is_chronological_once_the_spelling_is_uniform():
    """The property the whole scheme rests on."""
    base = datetime(2026, 7, 30, 23, 59, 59, tzinfo=timezone.utc)
    instants = [base + timedelta(seconds=s) for s in (0, 1, 2, 3600, 86400, -86400)]
    canonical = [i.strftime(ISO_Z_FORMAT) for i in instants]
    assert sorted(canonical) == [i.strftime(ISO_Z_FORMAT) for i in sorted(instants)]


def test_the_same_instants_in_mixed_spellings_sort_wrong():
    """The known-answer control for the test above.

    Without this, a uniform-format test passes for any format at all, including the broken
    one, because it never sees a mix.

    My first version of this control was wrong and passed for the wrong reason. It compared
    a space-form 12:00:00 against a ``T``-form 12:00:01, where the text order and the clock
    order AGREE, so it proved nothing. The disagreement needs the later instant written in
    the space form: 23:00 with a space sorts below 09:00 with a ``T``.
    """
    late_space = "2026-07-30 23:00:00+00"    # the LATER instant
    early_t = "2026-07-30T09:00:00Z"         # the EARLIER instant
    assert sorted([early_t, late_space]) == [late_space, early_t], (
        "the mix no longer sorts wrong; this control has stopped controlling anything")


def test_no_column_in_the_schema_still_carries_the_old_default():
    """A drift guard. A new column with ``func.now()`` reintroduces the whole defect."""
    offenders = []
    canonical = 0
    for table in sa_schema.metadata.sorted_tables:
        for col in table.columns:
            default = col.server_default
            if default is None:
                continue
            arg = getattr(default, "arg", None)
            if isinstance(arg, utc_now_iso_sql):
                canonical += 1
                continue
            # Compare by TYPE above, never by rendered string: utc_now_iso_sql falls back to
            # CURRENT_TIMESTAMP under the default dialect, so a string test reads it as an
            # offender. The first version of this test did exactly that and failed on 162
            # columns it had itself just fixed.
            if str(arg).strip().lower() in {"now()", "current_timestamp"}:
                offenders.append(f"{table.name}.{col.name}")
    assert not offenders, (
        f"{len(offenders)} columns still default to now(): {offenders[:5]}")
    # The power check. Zero offenders also describes an empty schema.
    assert canonical >= 150, f"only {canonical} columns carry the canonical default"


def test_the_sqlite_default_writes_the_canonical_spelling():
    md = MetaData()
    probe = Table("ts_probe", md,
                  Column("id", Text, primary_key=True),
                  Column("created_at", Text, nullable=False,
                         server_default=utc_now_iso_sql()))
    engine = create_engine("sqlite://")
    md.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO ts_probe (id) VALUES ('a')"))
        value = conn.execute(text("SELECT created_at FROM ts_probe")).scalar()
    assert _CANON.match(value), f"SQLite default is not canonical: {value!r}"


@pytest.mark.parametrize("spelling", _SPELLINGS)
def test_the_sqlite_backfill_expression_normalizes_every_spelling(spelling):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (v TEXT)"))
        conn.execute(text("INSERT INTO t (v) VALUES (:v)"), {"v": spelling})
        got = conn.execute(text(f"SELECT {canonical_sql('v', 'sqlite')} FROM t")).scalar()
    if spelling.endswith("+00"):
        # SQLite's date functions reject a two-digit offset. That is the PostgreSQL
        # spelling, so it reaches a SQLite database only through an import, and leaving it
        # untouched is safer than guessing.
        assert got is None
        return
    assert got == "2026-07-30T12:00:00Z", f"{spelling!r} normalized to {got!r}"


def test_the_sqlite_backfill_leaves_an_unreadable_value_alone():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (v TEXT)"))
        conn.execute(text("INSERT INTO t (v) VALUES ('not a timestamp')"))
        got = conn.execute(text(f"SELECT {canonical_sql('v', 'sqlite')} FROM t")).scalar()
    assert got is None, f"an unreadable value was converted to {got!r}"


# ---------------------------------------------------------------------------
# Migration 090, the SQLite half
# ---------------------------------------------------------------------------
_MIGRATION_SQL = "090_canonical_text_timestamps.sql"


def _migration_text() -> str:
    """Read the file the way ``connection.py`` does. ``db.migrations`` has no ``__file__``."""
    from importlib import resources

    return (resources.files("sable_platform.db.migrations") / _MIGRATION_SQL).read_text(
        encoding="utf-8")


def _run_090(conn):
    """Execute the migration file exactly the way ``connection.py`` does."""
    for stmt in [s.strip() for s in _migration_text().split(";") if s.strip()]:
        conn.execute(stmt)
    conn.commit()


def test_migration_090_is_registered_for_sqlite():
    """The dual-migration rule in CLAUDE.md. An Alembic revision alone drifts the dialects."""
    from sable_platform.db.connection import _MIGRATIONS

    assert (_MIGRATION_SQL, 90) in _MIGRATIONS


def test_the_migration_file_has_no_semicolon_inside_a_comment():
    """``connection.py`` splits the file on ``;`` with no SQL parser.

    A semicolon inside a ``--`` comment cuts the comment in half and feeds the tail to
    SQLite as a statement.
    """
    offenders = [ln for ln in _migration_text().split("\n")
                 if ln.lstrip().startswith("--") and ";" in ln]
    assert not offenders, offenders


@pytest.mark.parametrize("spelling,expected", [
    ("2026-07-30 23:00:00", "2026-07-30T23:00:00Z"),
    ("2026-07-30T23:00:00Z", "2026-07-30T23:00:00Z"),
    ("2026-07-30 23:00:00.123456", "2026-07-30T23:00:00Z"),
    ("2026-07-30T16:00:00-07:00", "2026-07-30T23:00:00Z"),
    ("2026-07-30 23:00:00+00", None),      # SQLite rejects a two-digit offset; left alone
    ("not a timestamp", None),             # unreadable; left alone
])
def test_migration_090_normalizes_what_it_can_read_and_leaves_the_rest(spelling, expected):
    engine = create_engine("sqlite:///:memory:")
    sa_schema.metadata.create_all(engine)
    raw = engine.raw_connection().driver_connection
    raw.execute("INSERT INTO orgs (org_id, display_name, created_at, updated_at)"
                " VALUES ('o1','O',?,?)", (spelling, spelling))
    raw.commit()
    _run_090(raw)
    got = raw.execute("SELECT created_at FROM orgs WHERE org_id='o1'").fetchone()[0]
    assert got == (expected if expected is not None else spelling)


def test_migration_090_is_idempotent():
    engine = create_engine("sqlite:///:memory:")
    sa_schema.metadata.create_all(engine)
    raw = engine.raw_connection().driver_connection
    raw.execute("INSERT INTO orgs (org_id, display_name, created_at, updated_at)"
                " VALUES ('o1','O','2026-07-30 23:00:00','2026-07-30 23:00:00')")
    raw.commit()
    _run_090(raw)
    once = raw.execute("SELECT created_at FROM orgs WHERE org_id='o1'").fetchone()[0]
    _run_090(raw)
    twice = raw.execute("SELECT created_at FROM orgs WHERE org_id='o1'").fetchone()[0]
    assert once == twice == "2026-07-30T23:00:00Z"


# ---------------------------------------------------------------------------
# The drift guard on the WRITE side
# ---------------------------------------------------------------------------
# `api_tokens` is the one file that keeps a bare CURRENT_TIMESTAMP, and the reason is in
# the file: its `created_at`, `last_used_at`, `revoked_at` and `expires_at` are
# `timestamp with time zone` on PostgreSQL, so writing the canonical TEXT expression into
# one is a hard error. DEFECTS_FOUND item 9.
_CURRENT_TIMESTAMP_ALLOWED = {"sable_platform/api/tokens.py"}


def test_no_module_writes_a_bare_current_timestamp():
    """The write half of the defect, guarded.

    Canonicalizing the DEFAULT is only half a fix. `UPDATE ... SET updated_at =
    CURRENT_TIMESTAMP` writes the SPACE form on both dialects, so 46 statements across 16
    files were reintroducing the mix on every update.
    """
    import pathlib

    import sable_platform

    root = pathlib.Path(sable_platform.__file__).parent
    offenders = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root.parent).as_posix()
        if "_vendor" in rel or "/alembic/" in rel or rel.endswith("ts_format.py"):
            continue
        if rel in _CURRENT_TIMESTAMP_ALLOWED:
            continue
        for num, line in enumerate(path.read_text().split("\n"), 1):
            if "CURRENT_TIMESTAMP" not in line:
                continue
            if line.lstrip().startswith("#") or line.lstrip().startswith("``"):
                continue        # a comment describing the defect, not a writer
            offenders.append(f"{rel}:{num}")
    assert not offenders, f"bare CURRENT_TIMESTAMP writers: {offenders}"


def _backfill_one(stored):
    engine = create_engine("sqlite:///:memory:")
    sa_schema.metadata.create_all(engine)
    raw = engine.raw_connection().driver_connection
    raw.execute("INSERT INTO orgs (org_id, display_name, created_at, updated_at)"
                " VALUES ('o1','O',?,?)", (stored, stored))
    raw.commit()
    _run_090(raw)
    return raw.execute("SELECT created_at FROM orgs WHERE org_id='o1'").fetchone()[0]


@pytest.mark.parametrize("stored", [" 2026-07-30T23:00:00Z ", "  2026-07-30 23:00:00 "])
def test_migration_090_strips_space_padding_from_a_canonical_value(stored):
    """Codex round 5, MAJOR.

    A space-padded canonical value TRIMS to something canonical, so a TRIM-based "is it
    already canonical" test skipped the row and left the padding in the column, where it
    still sorts wrong and was never counted.
    """
    assert _backfill_one(stored) == "2026-07-30T23:00:00Z", f"{stored!r} kept its padding"


def test_migration_090_leaves_a_tab_padded_value_alone_and_that_is_the_limit():
    """The stated limit on the fix above, not a claim that all whitespace is handled.

    ``TRIM`` with no character set strips SPACES on both dialects, never tabs or newlines.
    Widening it here would put the write path out of step with
    ``compat._pg_instant``, which is what every READ goes through. A tab-padded value is
    therefore left as it is and shows up in the migration's "left unreadable" count, which
    is visible rather than silent.
    """
    stored = "\t2026-07-30 23:00:00\n"
    assert _backfill_one(stored) == stored


def test_migration_090_skips_a_shape_matching_value_that_is_not_a_real_date():
    """A SHAPE is not a date. '2026-13-01T00:00:00Z' matches the parseable pattern."""
    engine = create_engine("sqlite:///:memory:")
    sa_schema.metadata.create_all(engine)
    raw = engine.raw_connection().driver_connection
    bad = "2026-13-01T00:00:00Z"
    raw.execute("INSERT INTO orgs (org_id, display_name, created_at, updated_at)"
                " VALUES ('o1','O',?,?)", (bad, bad))
    raw.commit()
    _run_090(raw)
    got = raw.execute("SELECT created_at FROM orgs WHERE org_id='o1'").fetchone()[0]
    assert got == bad, f"an out-of-range month was converted to {got!r}"
