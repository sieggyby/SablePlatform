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
