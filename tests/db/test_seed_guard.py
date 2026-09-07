"""``resolve_seed_target``: a dry run must not create a database, a typo must not seed one.

THE MEASURED DEFECT, on 2026-09-06 before the guard existed::

    $ SABLE_DB_PATH=/tmp/probe.db python scripts/seed_robotmoney.py --dry-run
    Target DB: sqlite:////tmp/probe.db
    DRY RUN - 4 actions planned, 0 written.
    $ ls -l /tmp/probe.db
    -rw-r--r--  1,900,544 bytes        # 144 tables

Exit 0, "0 written", 1.9 MB on disk. The seed opens a connection to build its plan, and
opening the platform database creates and migrates it.

WHAT THIS GUARD NO LONGER CLAIMS. Its ancestor existed to stop a seed BRICKING a fresh
database, because the seeds built from ``schema.py`` via ``metadata.create_all`` and left
no ``schema_version`` row. That path is deleted. A database created here is now correct,
and ``test_a_created_database_is_schema_correct_and_opens_under_get_db`` proves it rather
than asserting it. The refusal survives for a different reason, and the messages say the
new one.

The subprocess tests set ``SABLE_DB_PATH`` into ``tmp_path`` and clear
``SABLE_DATABASE_URL``. Clearing the URL alone is NOT enough: the resolver then falls
through to ``~/.sable/sable.db``, which is the real 183 MB production-shaped database on
this machine. They also set ``PYTHONPATH``, because running a script BY PATH puts the
script's directory on ``sys.path`` and not the working directory, so ``sable_platform``
would resolve to whichever checkout happens to be installed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from sable_platform.db.seed_guard import resolve_seed_target

REPO = Path(__file__).resolve().parents[2]
SEED = REPO / "scripts" / "seed_robotmoney.py"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A target inside tmp_path, with every route to the real database closed."""
    monkeypatch.delenv("SABLE_DATABASE_URL", raising=False)
    monkeypatch.setenv("SABLE_DB_PATH", str(tmp_path / "sable.db"))
    monkeypatch.setenv("SABLE_OPERATOR_ID", "test")
    return tmp_path / "sable.db"


def _run_seed(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "SABLE_DB_PATH": str(tmp_path / "sable.db"),
        "SABLE_OPERATOR_ID": "test",
        "PYTHONPATH": str(REPO),  # the sys.path trap: a script by path shadows cwd
    }
    return subprocess.run(
        [sys.executable, str(SEED), *args],
        capture_output=True, text=True, cwd=str(REPO), env=env, timeout=180,
    )


# ---------------------------------------------------------------------------
# The two refusals
# ---------------------------------------------------------------------------


def test_a_dry_run_against_a_missing_database_is_refused(sandbox):
    """The measured defect. A dry run that creates 1.9 MB is not a dry run."""
    assert not sandbox.exists()

    with pytest.raises(SystemExit) as exc:
        resolve_seed_target(dry_run=True)

    assert "--dry-run cannot run against" in str(exc.value)
    assert not sandbox.exists(), "the refusal itself created the database"


def test_a_missing_database_is_refused_without_the_flag(sandbox):
    """A missing target is more often a wrong path than a missing database."""
    with pytest.raises(SystemExit) as exc:
        resolve_seed_target()

    assert "Refusing to create" in str(exc.value)
    assert not sandbox.exists()


def test_dry_run_and_create_db_together_are_refused(sandbox):
    """One says write nothing and the other says write a database.

    Refused BEFORE the target is resolved, so it cannot depend on whether the file exists.
    """
    with pytest.raises(SystemExit) as exc:
        resolve_seed_target(dry_run=True, allow_create=True)

    assert "contradict each other" in str(exc.value)


def test_the_contradiction_is_refused_even_when_the_database_exists(sandbox):
    """The pair is incoherent on its own terms, not only when it would create something.

    Without this the check could be moved below the existence test and still pass every
    other test in this file.
    """
    sandbox.parent.mkdir(parents=True, exist_ok=True)
    sandbox.touch()

    with pytest.raises(SystemExit) as exc:
        resolve_seed_target(dry_run=True, allow_create=True)
    assert "contradict each other" in str(exc.value)


# ---------------------------------------------------------------------------
# What must pass through untouched
# ---------------------------------------------------------------------------


def test_an_existing_database_passes_through(sandbox):
    sandbox.parent.mkdir(parents=True, exist_ok=True)
    sandbox.touch()

    assert resolve_seed_target() == f"sqlite:///{sandbox}"
    assert resolve_seed_target(dry_run=True) == f"sqlite:///{sandbox}"


def test_postgresql_is_never_refused(sandbox):
    """Prod is PostgreSQL, and the deploy-time replay step runs these scripts against it.

    A file-existence check must not fire on a URL that names no file.
    """
    url = "postgresql://u:p@localhost:5432/sable"
    assert resolve_seed_target(url) == url
    assert resolve_seed_target(url, dry_run=True) == url


def test_an_in_memory_database_is_never_refused(sandbox):
    assert resolve_seed_target("sqlite://") == "sqlite://"
    assert resolve_seed_target("sqlite:///:memory:") == "sqlite:///:memory:"


def test_create_db_allows_it_and_says_so(sandbox, capsys):
    """The NOTE names the path, because the path is what the operator can get wrong."""
    assert resolve_seed_target(allow_create=True) == f"sqlite:///{sandbox}"

    warned = capsys.readouterr().err
    assert "creating a new database" in warned
    assert str(sandbox) in warned
    assert "schema-correct" in warned, (
        "the old message promised a BROKEN database; that claim is now false"
    )


def test_an_explicit_url_beats_the_environment(sandbox, tmp_path):
    """--url must win, or the guard would report on one target and the seed open another."""
    other = tmp_path / "other.db"
    other.touch()

    assert resolve_seed_target(f"sqlite:///{other}") == f"sqlite:///{other}"


# ---------------------------------------------------------------------------
# End to end, through the actual script
# ---------------------------------------------------------------------------


def test_the_script_refuses_a_dry_run_and_writes_nothing(tmp_path):
    """KNOWN ANSWER: the exact invocation that produced 1.9 MB and reported '0 written'."""
    result = _run_seed(tmp_path, "--dry-run")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "--dry-run cannot run against" in (result.stdout + result.stderr)
    assert not (tmp_path / "sable.db").exists(), "the dry run created a database"
    assert "ImportError" not in result.stderr, "the subprocess imported the wrong checkout"


def test_a_created_database_is_schema_correct_and_opens_under_get_db(tmp_path):
    """DEFECTS_FOUND item 12, proven end to end rather than asserted.

    The old path produced 144 tables and NO schema_version row, and the next command died
    on ``duplicate column name: cult_run_id``. This runs the real script and then opens the
    result the way every other tool opens it.
    """
    import sqlite3

    result = _run_seed(tmp_path, "--create-db")
    assert result.returncode == 0, result.stdout + result.stderr

    db = tmp_path / "sable.db"
    assert db.exists()
    con = sqlite3.connect(str(db))
    try:
        tables = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        version = con.execute("SELECT version FROM schema_version").fetchone()
        orgs = con.execute("SELECT COUNT(*) FROM orgs").fetchone()[0]
    finally:
        con.close()

    assert version is not None and version[0] > 0, (
        f"no schema_version row: this is the old create_all path, {tables} tables"
    )
    assert tables > 100, tables
    assert orgs > 0, "the seed created the database but wrote no rows"

    from sable_platform.db.connection import get_db

    conn = get_db(str(db))
    try:
        conn.execute("SELECT COUNT(*) FROM orgs").fetchone()
    finally:
        conn.close()


def test_a_refusal_leaves_no_trace_on_disk(tmp_path, monkeypatch):
    """A gate found this. Refusing a target must not create its parent directory either.

    ``resolve_platform_url`` used to mkdir while RESOLVING, so a refused run still left the
    directory behind: the guard said no and the filesystem said something happened anyway.
    Directory creation now belongs to the code that opens a database.
    """
    monkeypatch.delenv("SABLE_DATABASE_URL", raising=False)
    missing_dir = tmp_path / "never" / "made"
    monkeypatch.setenv("SABLE_DB_PATH", str(missing_dir / "sable.db"))

    for kwargs in ({"dry_run": True}, {}):
        with pytest.raises(SystemExit):
            resolve_seed_target(**kwargs)
        assert not missing_dir.exists(), f"a refusal with {kwargs} created {missing_dir}"
        assert not missing_dir.parent.exists()


def test_resolving_a_target_creates_nothing_at_all(tmp_path, monkeypatch):
    """The pure-resolution claim, stated directly rather than only through the guard."""
    from sable_platform.db.connection import resolve_platform_url

    monkeypatch.delenv("SABLE_DATABASE_URL", raising=False)
    nowhere = tmp_path / "nowhere"
    url = resolve_platform_url(db_path=str(nowhere / "x.db"))

    assert url == f"sqlite:///{nowhere / 'x.db'}"
    assert not nowhere.exists(), "resolve_platform_url created a directory"


def test_opening_a_target_still_creates_its_directory(tmp_path, monkeypatch):
    """POWER CHECK: the mkdir MOVED, it did not disappear.

    Without this, deleting the mkdir entirely would pass every test above while a fresh
    SABLE_DB_PATH failed with "unable to open database file".
    """
    from sable_platform.db.connection import get_raw_db

    monkeypatch.delenv("SABLE_DATABASE_URL", raising=False)
    fresh = tmp_path / "brand" / "new"
    with get_raw_db(url=f"sqlite:///{fresh / 'sable.db'}") as conn:
        conn.execute(text("SELECT 1"))

    assert (fresh / "sable.db").exists(), "the mkdir was lost, not moved"


# ---------------------------------------------------------------------------
# The seeds double as the PostgreSQL replay step, and could not do it twice
# ---------------------------------------------------------------------------

SEEDS = ("seed_general_ct.py", "seed_robotmoney.py")


def _sql_string_literals(source: str) -> list[str]:
    """Every string constant in *source* that is not a docstring.

    Docstrings are excluded on purpose: the code under test explains this defect by quoting
    the literal it must not emit, and a guard that bans its own explanation is worse than
    the hole it closes.
    """
    import ast

    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)

    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in docstrings
    ]



def test_the_seeds_emit_no_sqlite_only_timestamp():
    """A gate found this. ``strftime`` is SQLite-only and PostgreSQL has no such function.

    Both seeds document PostgreSQL replay in their module docstring: "set
    SABLE_DATABASE_URL=postgresql://... and re-run". Every idempotent re-run against
    PostgreSQL died the moment a row already existed and the INSERT fell through to an
    UPDATE carrying this literal.

    The literal they inlined is exactly ``ts_format.SQLITE_NOW_CANONICAL``, one half of a
    constant that already had a dialect-safe accessor in this codebase.

    IT CHECKS EXECUTABLE STRINGS, NOT PROSE. A plain substring version of this test failed
    on its first run against a DOCSTRING that quotes the literal while explaining the
    defect. A guard that forbids documenting the thing it forbids is wrong in the expensive
    direction, so this reads string constants from the AST and skips docstrings.
    """
    from sable_platform.db.ts_format import SQLITE_NOW_CANONICAL

    for name in SEEDS:
        for literal in _sql_string_literals((REPO / "scripts" / name).read_text()):
            assert SQLITE_NOW_CANONICAL not in literal, (
                f"{name} hardcodes the SQLite spelling in SQL; use _now_sql(conn)"
            )
            assert "strftime(" not in literal, f"{name} emits strftime in SQL"


def test_now_sql_actually_switches_on_the_dialect():
    """KNOWN ANSWER, both dialects. The guard above is a substring check and proves nothing
    about behaviour on its own: a seed could drop ``strftime`` and still emit SQLite-only
    SQL some other way.
    """
    from sable_platform.db.ts_format import PG_NOW_CANONICAL, SQLITE_NOW_CANONICAL

    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import seed_robotmoney
    finally:
        sys.path.pop(0)

    class _Dialect:
        def __init__(self, name):
            self.name = name

    class _Conn:
        def __init__(self, name):
            self.dialect = _Dialect(name)

    assert seed_robotmoney._now_sql(_Conn("postgresql")) == PG_NOW_CANONICAL
    assert "strftime" not in seed_robotmoney._now_sql(_Conn("postgresql"))
    assert seed_robotmoney._now_sql(_Conn("sqlite")) == SQLITE_NOW_CANONICAL


def test_a_second_run_reaches_the_update_branch(tmp_path):
    """REACHABILITY, and it is why the defect survived a SQLite-only suite for months.

    The two tests above would pass against dead code. This proves the UPDATE branch runs:
    the first seed INSERTs, the second finds the rows and takes the UPDATE path that carried
    the SQLite-only timestamp. On a first run the defect is unreachable, which is exactly
    why nobody hit it locally.
    """
    first = _run_seed(tmp_path, "--create-db")
    assert first.returncode == 0, first.stdout + first.stderr

    second = _run_seed(tmp_path)  # the database exists now, so no flag is needed
    assert second.returncode == 0, second.stdout + second.stderr
    assert "UPDATE orgs" in second.stdout, (
        "the second run did not take the UPDATE branch, so this proves nothing"
    )

    import sqlite3

    con = sqlite3.connect(str(tmp_path / "sable.db"))
    try:
        assert con.execute("SELECT COUNT(*) FROM orgs").fetchone()[0] == 1, (
            "the idempotent re-run duplicated the org"
        )
    finally:
        con.close()
