"""Shared connection factory and migration runner for sable.db.

Legacy ``get_db()`` returns a raw ``sqlite3.Connection``.  The new
``get_sa_engine()`` / ``get_sa_connection()`` functions return SQLAlchemy
objects and are the migration path toward Postgres support.
"""
from __future__ import annotations

import importlib.resources
import logging
import os
import sqlite3
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.engine import Connection as SAConnection

log = logging.getLogger(__name__)

_MIGRATIONS = [
    ("001_initial.sql", 1),
    ("002_sync_runs_run_id.sql", 2),
    ("003_diagnostic_runs_cult_columns.sql", 3),
    ("004_jobs_extend.sql", 4),
    ("005_artifacts_degraded.sql", 5),
    ("006_workflow_tables.sql", 6),
    ("007_actions_outcomes.sql", 7),
    ("008_entity_journey.sql", 8),
    ("009_alerts.sql", 9),
    ("010_discord_pulse_runs.sql", 10),
    ("011_alert_cooldown.sql", 11),
    ("012_workflow_version.sql", 12),
    ("013_alert_delivery_error.sql", 13),
    ("014_entity_interactions.sql", 14),
    ("015_entity_decay_scores.sql", 15),
    ("016_entity_centrality.sql", 16),
    ("017_entity_watchlist.sql", 17),
    ("018_audit_log.sql", 18),
    ("019_webhooks.sql", 19),
    ("020_prospect_scores.sql", 20),
    ("021_run_summary_blob.sql", 21),
    ("022_playbook_tagging.sql", 22),
    ("023_centrality_schema_align.sql", 23),
    ("024_operator_identity_and_indexes.sql", 24),
    ("025_prospect_graduation.sql", 25),
    ("026_prospect_rejection.sql", 26),
    ("027_workflow_active_lock.sql", 27),
    ("028_platform_meta.sql", 28),
    ("029_prospect_score_fields.sql", 29),
    ("030_performance_indexes.sql", 30),
]


def sable_db_path() -> Path:
    """Return the resolved path to sable.db (from ``SABLE_DB_PATH`` or default)."""
    env = os.environ.get("SABLE_DB_PATH")
    if env:
        return Path(env)
    return Path.home() / ".sable" / "sable.db"


# Keep private alias for any internal callers.
_sable_db_path = sable_db_path


def get_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _sable_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Apply pending migrations to bring sable.db up to current version."""
    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        current = row[0] if row else 0
    except sqlite3.OperationalError:
        current = 0

    migrations_pkg = importlib.resources.files("sable_platform.db") / "migrations"

    for filename, target_version in _MIGRATIONS:
        if current < target_version:
            sql_file = migrations_pkg / filename
            sql = sql_file.read_text(encoding="utf-8")
            stmts = [s.strip() for s in sql.split(";") if s.strip()]
            with conn:
                for stmt in stmts:
                    conn.execute(stmt)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
                    (target_version,),
                )
            current = target_version
            if target_version == 27:
                _warn_migration_027_autofails(conn)


def _warn_migration_027_autofails(conn: sqlite3.Connection) -> None:
    """Emit a log warning if migration 027 auto-failed any duplicate active runs."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM workflow_runs WHERE error LIKE 'auto-failed by migration 027%'"
        ).fetchone()
        n = row[0] if row else 0
        if n > 0:
            log.warning(
                "Migration 027: auto-failed %d duplicate active workflow run(s) — "
                "query workflow_runs WHERE error LIKE 'auto-failed by migration 027%%' for details",
                n,
            )
    except sqlite3.OperationalError:
        pass  # workflow_runs table absent — migration applied to empty DB


# ---------------------------------------------------------------------------
# SQLAlchemy connection path (new — coexists with legacy get_db)
# ---------------------------------------------------------------------------


def get_sa_engine(url: str | None = None) -> Engine:
    """Return a SQLAlchemy :class:`Engine` for the platform database.

    For SQLite engines the schema is created via :func:`metadata.create_all`
    (idempotent).  For Postgres, Alembic manages migrations separately.

    .. important::
        ``schema.py`` must stay in sync with the SQL migration files listed
        in ``_MIGRATIONS``.  The parity tests in ``tests/db/test_schema.py``
        verify this mechanically.
    """
    from sable_platform.db.engine import get_engine
    from sable_platform.db.schema import metadata

    engine = get_engine(url)
    if engine.dialect.name == "sqlite":
        metadata.create_all(engine)
    return engine


def get_sa_connection(url: str | None = None) -> SAConnection:
    """Convenience: return an open SQLAlchemy :class:`Connection`.

    Callers are responsible for calling ``conn.close()`` when done (or using
    the connection as a context manager).
    """
    return get_sa_engine(url).connect()
