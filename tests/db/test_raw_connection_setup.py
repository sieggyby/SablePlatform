"""``get_raw_db`` must do everything ``get_db`` does, minus the wrapper.

Two ways to open a raw connection existed, and only one of them worked on a fresh database.
``get_engine(url).connect()`` resolves the same URL as ``get_db`` but skips the SQLite parent
``mkdir`` and ``ensure_schema`` that ``get_db`` performs on the way, so the first query fails
with ``no such table``. ``get_raw_db`` is the one right answer; these tests keep it that way.

PostgreSQL is not affected by any of this. The setup runs only on the SQLite branch, so on
PostgreSQL ``get_raw_db`` and ``get_engine(url).connect()` are the same thing.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text

from sable_platform.cli import deck_cmds, relay_cmds
from sable_platform.db.connection import get_db, get_raw_db

# One table from each of the two CLI surfaces that open a raw connection.
PROBE_TABLES = ("relay_clients", "content_publish_jobs")

OPENERS = [
    ("get_raw_db", lambda: get_raw_db()),
    ("relay_cmds._connect", relay_cmds._connect),
    ("deck_cmds._connect", deck_cmds._connect),
]
OPENER_IDS = [name for name, _ in OPENERS]


@pytest.fixture(params=["SABLE_DB_PATH", "SABLE_DATABASE_URL"])
def missing_parent_target(request, tmp_path, monkeypatch):
    """A database file under a directory that does not exist, set BOTH ways.

    The two environment branches used to disagree: ``SABLE_DB_PATH`` created the parent and
    ``SABLE_DATABASE_URL=sqlite:///...`` did not, so the same missing directory worked
    through one and failed through the other.
    """
    target = tmp_path / "missing" / "sable.db"
    assert not target.parent.exists()
    monkeypatch.delenv("SABLE_DB_PATH", raising=False)
    monkeypatch.delenv("SABLE_DATABASE_URL", raising=False)
    if request.param == "SABLE_DB_PATH":
        monkeypatch.setenv("SABLE_DB_PATH", str(target))
    else:
        monkeypatch.setenv("SABLE_DATABASE_URL", f"sqlite:///{target}")
    return target


@pytest.mark.parametrize("opener", [o for _, o in OPENERS], ids=OPENER_IDS)
def test_a_raw_opener_creates_and_migrates_a_fresh_database(opener, missing_parent_target):
    """Validate against the BUG: the bare form raises ``unable to open database file``."""
    conn = opener()
    try:
        for table in PROBE_TABLES:
            assert conn.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar() == 0
    finally:
        conn.close()
    assert missing_parent_target.exists()


@pytest.mark.parametrize("opener", [o for _, o in OPENERS], ids=OPENER_IDS)
def test_a_raw_opener_returns_the_unwrapped_connection(opener, missing_parent_target):
    """The point of a raw opener: the SQLAlchemy transaction methods survive."""
    conn = opener()
    try:
        for method in ("in_transaction", "exec_driver_sql", "execution_options"):
            assert hasattr(conn, method)
    finally:
        conn.close()


def test_get_db_reaches_the_same_database_through_either_env_var(missing_parent_target):
    """``get_raw_db`` is held to ``get_db``'s contract, so ``get_db`` must meet it too."""
    conn = get_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM relay_clients").fetchone()[0] == 0
    finally:
        conn.close()
    assert missing_parent_target.exists()


def test_the_bare_form_is_what_fails(missing_parent_target, monkeypatch):
    """POWER CHECK: prove ``get_engine(url).connect()`` really does break here.

    Without this the tests above could be passing on a path that was never broken.
    """
    from sable_platform.cli.main import _resolve_cli_database_target
    from sable_platform.db.engine import get_engine

    target = _resolve_cli_database_target(None)
    with pytest.raises(Exception) as exc:
        with get_engine(target.connection_url).connect() as conn:
            conn.execute(text("SELECT COUNT(*) FROM relay_clients"))
    # Either the directory is missing, or it exists and the tables do not.
    assert "unable to open database file" in str(exc.value) or "no such table" in str(exc.value)


def test_db_health_does_not_migrate_the_database_it_inspects(tmp_path, monkeypatch):
    """``db-health`` REPORTS on a database. It must not upgrade the thing it is measuring.

    This is why ``cli/main.py`` still calls ``get_engine(...).connect()`` while the relay and
    deck helpers use ``get_raw_db``. ``get_raw_db`` runs ``ensure_schema`` on the SQLite
    branch, so db-health would silently migrate a stale database and then report it healthy.

    TWO EARLIER VERSIONS OF THIS TEST COULD NOT FAIL, and the second failure taught me the
    rationale I had written down was wrong:

    * The first read ``main.py`` and asserted the source CONTAINED
      ``get_engine(target.connection_url).connect()``. Leave that string in a comment, swap
      the executable line, and it still passes.
    * The second ran the command against a MISSING file and asserted nothing was created.
      That also passes under the swap, because ``db_health`` returns early for a missing
      SQLite path before it opens anything. So "swapping it would make 'Database not found'
      unreachable", which is what I had claimed in the docstring and the commit message, is
      simply false. The early return protects that case, not the choice of opener.

    The hazard is an EXISTING but unmigrated database, which is the one shape that reaches
    the connect and that the two openers treat differently.
    """
    from click.testing import CliRunner

    from sable_platform.cli.main import cli

    stale = tmp_path / "stale.db"
    stale.touch()  # exists, so the early return does not fire; zero tables
    monkeypatch.delenv("SABLE_DATABASE_URL", raising=False)
    monkeypatch.setenv("SABLE_OPERATOR_ID", "test")

    result = CliRunner().invoke(cli, ["db-health", "--db-path", str(stale), "--json"])
    # Exit 1 is CORRECT here: the database is unhealthy and db-health says so. The point of
    # this test is what it did NOT do to the file.
    assert result.exit_code == 1, result.output
    assert '"ok": false' in result.output

    con = sqlite3.connect(str(stale))
    try:
        tables = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
    finally:
        con.close()
    assert tables == 0, f"db-health migrated the database it was inspecting: {tables} tables"


def test_db_health_still_reports_a_missing_database(tmp_path, monkeypatch):
    """The early-return branch, kept because it is the behaviour operators rely on."""
    from click.testing import CliRunner

    from sable_platform.cli.main import cli

    missing = tmp_path / "nowhere" / "sable.db"
    monkeypatch.delenv("SABLE_DATABASE_URL", raising=False)
    monkeypatch.setenv("SABLE_OPERATOR_ID", "test")

    result = CliRunner().invoke(cli, ["db-health", "--db-path", str(missing), "--json"])
    assert "Database not found" in result.output, result.output
    assert not missing.exists()


def test_get_sa_engine_must_not_be_routed_through_prepared_engine(tmp_path):
    """This branch adds ``_prepared_engine``. It must not absorb ``get_sa_engine``.

    The two build DIFFERENT SQLite schemas. ``get_sa_engine`` calls ``metadata.create_all``
    from ``schema.py``. ``_prepared_engine`` calls ``ensure_schema``, which replays the SQL
    files listed in ``_MIGRATIONS``. The two are not interchangeable:

    * ``metadata.create_all`` writes no ``schema_version`` row, so ``ensure_schema`` reads
      the version as 0 and replays migration 1 onward onto tables that already exist.
      Measured: ``OperationalError: duplicate column name: cult_run_id``.
    * The migration path also creates the ``autocm_kb_chunks_fts`` FTS5 virtual table and
      its four shadow tables, which SQLAlchemy metadata cannot declare.

    Merging them is therefore a schema change, not a plumbing change. This is a tripwire on
    the function THIS branch introduces. It is deliberately not seed policy: which of the
    two paths the seed scripts should build from is ``DEFECTS_FOUND`` item 12, and that
    work sits on the follow-up branch.
    """
    import sqlite3

    from sable_platform.db.connection import ensure_schema, get_sa_engine

    db = tmp_path / "create_all.db"
    engine = get_sa_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version FROM schema_version")).fetchone() is None

    raw = sqlite3.connect(str(db))
    try:
        with pytest.raises(sqlite3.OperationalError, match="duplicate column"):
            ensure_schema(raw)
    finally:
        raw.close()


def test_the_two_schema_paths_really_do_differ(tmp_path, monkeypatch):
    """Name the difference, so the claim above is checked rather than asserted."""
    from sable_platform.db.connection import _prepared_engine, get_sa_engine

    def tables(engine):
        with engine.connect() as conn:
            return {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                )
            }

    from_schema_py = tables(get_sa_engine(f"sqlite:///{tmp_path / 'a.db'}"))
    monkeypatch.setenv("SABLE_DATABASE_URL", f"sqlite:///{tmp_path / 'b.db'}")
    from_migrations = tables(_prepared_engine())

    only_in_migrations = from_migrations - from_schema_py
    assert only_in_migrations == {
        "autocm_kb_chunks_fts",
        "autocm_kb_chunks_fts_config",
        "autocm_kb_chunks_fts_data",
        "autocm_kb_chunks_fts_docsize",
        "autocm_kb_chunks_fts_idx",
    }
    assert not from_schema_py - from_migrations


def test_postgresql_gets_no_sqlite_setup(monkeypatch):
    """The production dialect must be untouched by any of this.

    ``_prepared_engine`` runs the ``mkdir`` and ``ensure_schema`` only on the SQLite
    branch, so on PostgreSQL ``get_raw_db`` and ``get_engine(url).connect()`` are the same
    call. This is what makes the relay and deck changes a no-op in production.

    THE URL ASSERTION IS NOT DECORATION. An earlier version of this test discarded the
    ``url`` the fake was called with, so it could not fail if ``_prepared_engine`` resolved
    the WRONG target. A fake that reports "postgresql" no matter what it is handed still
    returns the sentinel, and ``ensure_schema`` still does not run, while the code under
    test quietly connects to the local SQLite default. Connecting to the wrong database is
    a worse production failure than running the setup, so the target is checked here.
    """
    from sable_platform.db import connection as conn_mod

    class _FakeDialect:
        name = "postgresql"

    class _FakeEngine:
        dialect = _FakeDialect()

        def raw_connection(self):  # pragma: no cover - reaching this IS the failure
            raise AssertionError("_prepared_engine ran the SQLite setup on PostgreSQL")

        def connect(self):
            return "sentinel-connection"

    monkeypatch.setenv("SABLE_DATABASE_URL", "postgresql://u:p@localhost:5432/sable")
    seen_urls = []

    def _fake_get_engine(url=None, **kw):
        seen_urls.append(url)
        return _FakeEngine()

    monkeypatch.setattr("sable_platform.db.engine.get_engine", _fake_get_engine)
    called = []
    monkeypatch.setattr(conn_mod, "ensure_schema", lambda c: called.append(c))

    assert conn_mod.get_raw_db() == "sentinel-connection"
    assert called == [], "ensure_schema must not run on PostgreSQL"
    assert seen_urls == ["postgresql://u:p@localhost:5432/sable"], (
        f"_prepared_engine connected to the wrong target: {seen_urls}"
    )


def test_the_postgresql_guard_is_load_bearing(monkeypatch):
    """POWER CHECK: the same fake, reporting sqlite, MUST reach the setup it refuses above."""
    from sable_platform.db import connection as conn_mod

    class _FakeDialect:
        name = "sqlite"

    reached = []

    class _FakeEngine:
        dialect = _FakeDialect()

        def raw_connection(self):
            reached.append(True)
            raise AssertionError("stop here, the branch was taken")

        def connect(self):  # pragma: no cover - never reached
            return "sentinel-connection"

    monkeypatch.setenv("SABLE_DATABASE_URL", "sqlite:///power_check.db")
    seen_urls = []

    def _fake_get_engine(url=None, **kw):
        seen_urls.append(url)
        return _FakeEngine()

    monkeypatch.setattr("sable_platform.db.engine.get_engine", _fake_get_engine)
    with pytest.raises(AssertionError, match="stop here"):
        conn_mod.get_raw_db()
    assert reached == [True]
    assert seen_urls == ["sqlite:///power_check.db"], seen_urls


def test_sqlite_file_for_url_does_not_expand_a_tilde(tmp_path, monkeypatch):
    """SQLite treats ``~`` literally, so expanding it here would name the wrong file.

    Measured: ``create_engine("sqlite:///~/probe.db")`` writes ``./~/probe.db`` relative to
    the working directory. An ``expanduser`` in this helper pointed the ``mkdir`` at the home
    directory while the engine opened a directory called ``~``, and made the seed guard ask
    "does this exist" about a different file. ``sable_db_path`` does not expand either.
    """
    from sable_platform.db.connection import sqlite_file_for_url

    assert sqlite_file_for_url("sqlite:///~/probe.db") == Path("~/probe.db")
    assert str(sqlite_file_for_url("sqlite:///~/probe.db")).startswith("~")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("sqlite:///rel/x.db", "rel/x.db"),
        ("sqlite:////abs/x.db", "/abs/x.db"),
        ("sqlite+pysqlite:///rel/x.db", "rel/x.db"),
        ("sqlite+aiosqlite:///rel/x.db", "rel/x.db"),
        ("sqlite:///rel/x.db?mode=ro", "rel/x.db"),
        ("sqlite://", None),
        ("sqlite:///:memory:", None),
        ("postgresql://u:p@h/db", None),
        ("postgresql+psycopg2://u:p@h/db", None),
        ("not a url at all", None),
    ],
    ids=[
        "relative", "absolute", "pysqlite", "aiosqlite", "query-string",
        "bare-memory", "explicit-memory", "postgres", "postgres-driver", "malformed",
    ],
)
def test_sqlite_file_for_url_reads_every_form(url, expected):
    """One parser serves get_db's mkdir and both seed guards, so it has to be right."""
    from sable_platform.db.connection import sqlite_file_for_url

    result = sqlite_file_for_url(url)
    assert result == (Path(expected) if expected else None)
