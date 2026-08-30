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
    now_canonical_sql,
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
    Table("ts_probe", md,
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
# Nothing is allowed a bare CURRENT_TIMESTAMP any more. `api/tokens.py` was the one
# exception until migration 091; it now binds a canonical Python timestamp, which is the
# one form that is correct both before and after that migration.
_CURRENT_TIMESTAMP_ALLOWED: set[str] = set()


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


# --- Migration 092: the SQLite column DEFAULT -------------------------------------------

_MIGRATION_092_SQL = "092_sqlite_canonical_defaults.sql"
_STALE_DEFAULT = "datetime('now')"


def _ensure_schema_db():
    """A SQLite database built the way production builds one: by replaying _MIGRATIONS.

    NOT ``metadata.create_all``. The two disagree, and that disagreement is the defect this
    migration closes, so a test that builds from ``schema.py`` cannot see it.
    """
    import sqlite3

    from sable_platform.db.connection import ensure_schema

    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    return conn


def _timestamp_defaults(conn):
    """(canonical, stale) default expressions over every timestamp-named column."""
    canonical, stale = [], []
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        for row in conn.execute(f"PRAGMA table_info({table})"):
            name, default = row[1], row[4]
            if not (name.endswith("_at") or name in ("timestamp", "last_seen", "at_utc")):
                continue
            if default is None:
                continue
            target = canonical if ISO_Z_FORMAT in str(default) else stale
            target.append((table, name, str(default)))
    return canonical, stale


def test_migration_092_is_registered_for_sqlite():
    """The dual-migration rule in CLAUDE.md."""
    from sable_platform.db.connection import _MIGRATIONS

    assert (_MIGRATION_092_SQL, 92) in _MIGRATIONS


def test_no_sqlite_default_still_writes_the_old_spelling():
    """``ensure_schema`` replays SQL migrations. It does not build from ``schema.py``.

    A database created today therefore carries the DDL the early files wrote. Before
    migration 092 that was 48 columns across 42 tables defaulting to ``datetime('now')``,
    which renders a SPACE separator and no ``Z``.
    """
    conn = _ensure_schema_db()
    canonical, stale = _timestamp_defaults(conn)
    assert not stale, f"{len(stale)} stale defaults, e.g. {stale[:3]}"
    # Power check. A zero above is only meaningful if this database really has defaults.
    assert len(canonical) >= 150, (
        f"only {len(canonical)} canonical defaults found; the sweep is not seeing the schema")


def test_a_sqlite_default_writes_the_canonical_spelling_end_to_end():
    """The behaviour, not the DDL text. An INSERT that omits the column is the real case."""
    conn = _ensure_schema_db()
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('probe', 'P')")
    stored = conn.execute(
        "SELECT created_at FROM orgs WHERE org_id='probe'").fetchone()[0]
    assert _CANON.match(stored), f"the DEFAULT wrote {stored!r}"


def test_migration_092_leaves_the_database_intact():
    """A DDL text rewrite under ``writable_schema`` can corrupt a schema. Prove it did not."""
    conn = _ensure_schema_db()
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] >= 92


def test_migration_092_backfills_what_a_stale_default_wrote():
    """The DDL rewrite fixes future inserts. Rows written before it still need converting.

    Migration 090 canonicalized the values present when it ran, so the gap is a row a stale
    default inserted between 090 and 092. Built at version 91 on purpose, so the stale
    default is still in place when the row is written.
    """
    import sqlite3

    import sable_platform.db.connection as connection

    saved = connection._MIGRATIONS
    conn = sqlite3.connect(":memory:")
    try:
        connection._MIGRATIONS = [m for m in saved if m[1] <= 91]
        connection.ensure_schema(conn)
        conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('stale', 'S')")
        written = conn.execute(
            "SELECT created_at FROM orgs WHERE org_id='stale'").fetchone()[0]
        assert not _CANON.match(written), (
            f"the version-91 default already wrote {written!r}; this test proves nothing")
        # A value the backfill must refuse to convert.
        conn.execute("UPDATE orgs SET updated_at='not a timestamp' WHERE org_id='stale'")

        connection._MIGRATIONS = saved
        connection.ensure_schema(conn)
    finally:
        connection._MIGRATIONS = saved

    row = conn.execute(
        "SELECT created_at, updated_at FROM orgs WHERE org_id='stale'").fetchone()
    assert _CANON.match(row[0]), f"not converted: {row[0]!r}"
    # Same instant, one spelling. The separator is the only thing that changed.
    assert row[0] == written.replace(" ", "T") + "Z", f"{written!r} -> {row[0]!r}"
    assert row[1] == "not a timestamp", "the backfill converted a value it cannot read"


def test_migration_092_reaches_a_connection_that_was_already_open(tmp_path):
    """A direct ``sqlite_master`` UPDATE does not bump ``PRAGMA schema_version``.

    Without a bump, a connection opened BEFORE the migration keeps its cached schema and
    keeps writing the old default. This is a file database with two connections, because an
    in-memory database with one connection cannot show it: ``writable_schema=RESET`` reloads
    the connection that ran the migration, and that is the only one such a test has.
    """
    import sqlite3

    import sable_platform.db.connection as connection

    path = str(tmp_path / "sable.db")
    saved = connection._MIGRATIONS
    try:
        connection._MIGRATIONS = [m for m in saved if m[1] <= 91]
        writer = sqlite3.connect(path)
        connection.ensure_schema(writer)

        reader = sqlite3.connect(path)
        reader.execute("SELECT count(*) FROM orgs").fetchone()   # load the old schema
        version_before = writer.execute("PRAGMA schema_version").fetchone()[0]

        connection._MIGRATIONS = saved
        connection.ensure_schema(writer)
    finally:
        connection._MIGRATIONS = saved

    version_after = writer.execute("PRAGMA schema_version").fetchone()[0]
    assert version_after != version_before, (
        "PRAGMA schema_version did not move; open connections keep the old default")

    reader.execute("INSERT INTO orgs (org_id, display_name) VALUES ('viaReader', 'R')")
    reader.commit()
    stored = reader.execute(
        "SELECT created_at FROM orgs WHERE org_id='viaReader'").fetchone()[0]
    assert _CANON.match(stored), f"the pre-existing connection wrote {stored!r}"

    # The touch table must not survive.
    assert writer.execute(
        "SELECT count(*) FROM sqlite_master WHERE name='_sable_092_schema_touch'"
    ).fetchone()[0] == 0
    assert writer.execute("PRAGMA writable_schema").fetchone()[0] == 0
    assert writer.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_migration_092_fails_loudly_on_a_touch_table_collision():
    """The schema-cookie bump must never destroy a table that already holds data.

    ``CREATE TABLE IF NOT EXISTS`` followed by ``DROP TABLE IF EXISTS`` would skip the
    create and then drop someone else's table. 092 uses the bare forms, so a collision
    raises and the whole migration rolls back.
    """
    import sqlite3

    import sable_platform.db.connection as connection

    saved = connection._MIGRATIONS
    conn = sqlite3.connect(":memory:")
    try:
        connection._MIGRATIONS = [m for m in saved if m[1] <= 91]
        connection.ensure_schema(conn)
        conn.execute("CREATE TABLE _sable_092_schema_touch (payload TEXT)")
        conn.execute("INSERT INTO _sable_092_schema_touch VALUES ('do not lose me')")
        conn.commit()

        connection._MIGRATIONS = saved
        with pytest.raises(sqlite3.OperationalError):
            connection.ensure_schema(conn)
    finally:
        connection._MIGRATIONS = saved

    # The data survives and the database stays where it was.
    assert conn.execute(
        "SELECT payload FROM _sable_092_schema_touch").fetchone()[0] == "do not lose me"
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 91


def test_migration_092_rewrites_only_the_tables_it_names():
    """The rewrite is scoped by table NAME, not by "any DDL containing the string".

    An unscoped rewrite would also edit a CHECK constraint or a quoted literal in a table
    added later, and ``PRAGMA integrity_check`` would still return ok because the rewritten
    DDL stays valid. This plants exactly that table and proves 092 leaves it alone.
    """
    import sqlite3

    import sable_platform.db.connection as connection

    saved = connection._MIGRATIONS
    conn = sqlite3.connect(":memory:")
    # A DOUBLE-quoted literal. SQLite accepts it, and the stored DDL then carries the raw
    # string `datetime('now')`, which is exactly what an unscoped rewrite would match.
    ddl = ('CREATE TABLE bystander (note TEXT NOT NULL'
           ' CHECK (note <> "datetime(\'now\')"))')
    try:
        connection._MIGRATIONS = [m for m in saved if m[1] <= 91]
        connection.ensure_schema(conn)
        conn.execute(ddl)
        conn.commit()
        before = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='bystander'").fetchone()[0]
        assert "datetime('now')" in before, "the planted table does not carry the string"

        connection._MIGRATIONS = saved
        connection.ensure_schema(conn)
    finally:
        connection._MIGRATIONS = saved

    after = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='bystander'").fetchone()[0]
    assert after == before, f"092 rewrote a table it does not name:\n{before}\n{after}"
    # And it still did its real job.
    _canonical, stale = _timestamp_defaults(conn)
    assert not stale, stale


def test_the_092_name_list_matches_every_table_that_needs_it():
    """The scoped rewrite names 42 tables. A 43rd would be silently skipped.

    Scoping by name is what makes the migration enforce its own safety claim, and it buys
    that with a list that can go stale. This pins the list to the schema: build a database at
    version 91, ask it which tables actually carry the old default, and require the SQL file
    to name exactly those.

    A migration that adds a 43rd offender fails here, naming the table, rather than in
    ``test_no_sqlite_default_still_writes_the_old_spelling``, which would only report that a
    stale default survived.
    """
    import importlib.resources
    import re
    import sqlite3

    import sable_platform.db.connection as connection

    saved = connection._MIGRATIONS
    conn = sqlite3.connect(":memory:")
    try:
        connection._MIGRATIONS = [m for m in saved if m[1] <= 91]
        connection.ensure_schema(conn)
    finally:
        connection._MIGRATIONS = saved
    needs_it = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master"
        " WHERE type='table' AND sql LIKE '%datetime(''now'')%'")}

    sql = (importlib.resources.files("sable_platform.db") / "migrations"
           / _MIGRATION_092_SQL).read_text(encoding="utf-8")
    clause = sql.split("AND name IN (")[1].split(")")[0]
    named = set(re.findall(r"'([a-z0-9_]+)'", clause))

    assert named == needs_it, (
        f"named but not affected: {sorted(named - needs_it)}; "
        f"affected but NOT named: {sorted(needs_it - named)}")
    # Power check: an empty list on both sides would satisfy the equality above.
    assert len(needs_it) >= 40, f"only {len(needs_it)} tables found; the sweep is broken"


def test_the_postgresql_mask_is_not_read_as_a_bind_parameter():
    """`HH24:MI:SS` inside a `text()` string must not become bind parameters.

    SQLAlchemy reads `:name` in a `text()` construct as a bind parameter, and the PostgreSQL
    `to_char` mask is full of colons. It is safe today because the bind pattern ignores a
    colon preceded by a word character, and every colon in the mask follows one: `HH24:`,
    `MI:`. That is a property of the mask, not a guarantee, so it is pinned here.

    Edit the mask to put a space or a quote before a colon and this test fails, before the
    statement reaches a server and fails there instead.
    """
    for dialect in ("postgresql", "sqlite"):
        now_sql = text(f"SELECT {now_canonical_sql(dialect)}")
        cmp_sql = text(f"SELECT {canonical_sql('some_column', dialect)}")
        assert not now_sql._bindparams, (dialect, dict(now_sql._bindparams))
        assert not cmp_sql._bindparams, (dialect, dict(cmp_sql._bindparams))

    # Control. Without this, a change that stopped detecting binds entirely would pass.
    assert list(text("SELECT :real_param")._bindparams) == ["real_param"]
    # And the mixed shape every caller actually writes: mask plus a genuine bind.
    mixed = text(f"UPDATE t SET updated_at = {now_canonical_sql('postgresql')}"
                 f" WHERE id = :row_id")
    assert list(mixed._bindparams) == ["row_id"], dict(mixed._bindparams)


def test_the_pinned_migration_masks_are_not_read_as_bind_parameters():
    """Same hazard in the two Alembic migrations, which pin their own copy of the mask.

    ``op.execute()`` coerces a plain string into ``text()``, so a mask that parsed as a bind
    parameter would break the migration rather than a query. Every ``to_char`` expression in
    both files is checked, not only the pinned constant, because 090 also builds one inline.
    """
    import pathlib
    import re as _re

    root = pathlib.Path(sa_schema.__file__).parent.parent / "alembic" / "versions"
    files = ["b0c1d2e3f090_canonical_text_timestamps.py",
             "c1d2e3f4a091_text_timestamp_type_parity.py"]
    checked = 0
    for filename in files:
        source = (root / filename).read_text(encoding="utf-8")
        # Collapse the f-string line continuations so a wrapped expression reads as one.
        flat = _re.sub(r'"\s*\n\s*f?"', "", source).replace('\\"', '"')
        for match in _re.finditer(r"to_char\([^\n]*?HH24:MI:SS[^\n]*?\)", flat):
            expr = match.group(0)
            assert not text(f"SELECT {expr}")._bindparams, (filename, expr)
            checked += 1
    assert checked >= 3, f"only {checked} to_char expressions found; the scan is too narrow"


def test_migration_092_has_no_semicolon_inside_a_comment():
    """Same hazard as 090. ``connection.py`` splits on ``;`` with no SQL parser."""
    import importlib.resources

    sql = (importlib.resources.files("sable_platform.db") / "migrations"
           / _MIGRATION_092_SQL).read_text(encoding="utf-8")
    offenders = [ln for ln in sql.split("\n")
                 if ln.lstrip().startswith("--") and ";" in ln]
    assert not offenders, offenders
