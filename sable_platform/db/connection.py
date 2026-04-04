"""Shared SQLite connection factory and migration runner for sable.db."""
from __future__ import annotations

import importlib.resources
import os
import sqlite3
from pathlib import Path

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
