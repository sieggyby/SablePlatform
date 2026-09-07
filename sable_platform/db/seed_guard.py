"""Target resolution for the ``scripts/seed_*.py`` tools, with the refusals they need.

Seeding is the one operator task that routinely runs against a database the operator did
not name explicitly, so a wrong target is easy to reach and expensive to notice. This
module wraps :func:`sable_platform.db.connection.resolve_platform_url` with two refusals.

**A dry run must not create a database.** Measured on 2026-09-06, before this existed:

    $ SABLE_DB_PATH=/tmp/probe.db python scripts/seed_robotmoney.py --dry-run
    Target DB: sqlite:////tmp/probe.db
    DRY RUN - 4 actions planned, 0 written.
    $ ls -l /tmp/probe.db
    -rw-r--r--  1,900,544 bytes        # 144 tables

Exit 0, "0 written", and 1.9 MB on disk. The seed opens a connection to build its plan,
and opening the platform database creates and migrates it. A dry run against a database
that does not exist has nothing to plan against, so this refuses rather than inventing one.

**A typo in a path must not silently seed a new database.** Nothing else warns you. The
script prints its target and proceeds, and the operator discovers the mistake later by
finding an empty database where the data should be. ``--create-db`` is the explicit opt-in.

WHAT THIS DELIBERATELY NO LONGER CLAIMS. An earlier version of this guard existed to stop
a seed from BRICKING a fresh database. That was real: the seeds built the SQLite schema
from ``schema.py`` through ``metadata.create_all``, which writes no ``schema_version`` row,
so the next command replayed migration 1 onto existing tables and failed with
``duplicate column name: cult_run_id``. That path is deleted, the seeds now build through
``ensure_schema`` like everything else, and a database created here is a correct one. The
old refusal message said otherwise, and shipping it would have been a false claim standing
next to right code.
"""
from __future__ import annotations

import sys

from sable_platform.db.connection import resolve_platform_url, sqlite_file_for_url


def resolve_seed_target(
    explicit_url: str | None = None,
    *,
    allow_create: bool = False,
    dry_run: bool = False,
) -> str:
    """Resolve the seed's target URL, refusing the two cases above.

    Returns the URL to open. Raises :class:`SystemExit` with an operator-facing message
    when it refuses, because these run unattended as often as by hand and a warning on
    stderr needs a reader.

    PostgreSQL, an in-memory database, and any SQLite file that already exists all pass
    through untouched.
    """
    if dry_run and allow_create:
        raise SystemExit(
            "--dry-run and --create-db contradict each other.\n"
            "--dry-run writes nothing. --create-db writes a database.\n"
            "Drop one of them."
        )

    url = resolve_platform_url(url=explicit_url) if explicit_url else resolve_platform_url()

    database = sqlite_file_for_url(url)
    if database is None or database.exists():
        return url

    if dry_run:
        raise SystemExit(
            f"--dry-run cannot run against {database}, which does not exist.\n"
            "A dry run reads the database to build its plan, and opening the platform\n"
            "database CREATES it. Reporting '0 written' while writing a new database is\n"
            "the bug this refusal exists to prevent.\n\n"
            "Create it first, for example:\n"
            "    sable-platform init\n"
            "then re-run with --dry-run."
        )

    if not allow_create:
        raise SystemExit(
            f"Refusing to create {database}.\n"
            "It does not exist, so seeding here would build a NEW database and populate\n"
            "it. If you meant an existing one, this is a wrong path rather than a missing\n"
            "database, and nothing later would tell you so.\n\n"
            "Create it first, for example:\n"
            "    sable-platform init\n"
            "or pass --create-db to create it here."
        )

    # --create-db was passed, so say plainly what is about to happen. The database itself
    # will be correct: it is built by the same ensure_schema path every other command uses.
    # The risk being flagged is the PATH, not the schema.
    print(
        f"NOTE: creating a new database at {database}.\n"
        "      It will be schema-correct. Check the path is the one you meant.",
        file=sys.stderr,
    )
    return url
