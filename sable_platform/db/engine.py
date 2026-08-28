"""SQLAlchemy engine factory for sable.db.

Reads ``SABLE_DATABASE_URL`` when set, otherwise falls back to the existing
SQLite path (``SABLE_DB_PATH`` / ``~/.sable/sable.db``).  SQLite connections
automatically get WAL mode, foreign-key enforcement, and a 5-second busy
timeout via event listeners — matching the PRAGMAs in the legacy
``get_db()`` path.
"""
from __future__ import annotations

import os
import threading

from sqlalchemy import Engine, create_engine, event

from sable_platform.db.connection import sable_db_path

_engine_lock = threading.Lock()
_engine_cache: dict[str, Engine] = {}


def get_engine(url: str | None = None) -> Engine:
    """Return a :class:`sqlalchemy.Engine` for the platform database.

    Engines are cached by URL so repeated calls reuse the same pool.

    Resolution order for the connection URL:

    1. Explicit *url* argument (useful in tests).
    2. ``SABLE_DATABASE_URL`` environment variable.
    3. SQLite file at :func:`sable_db_path`.
    """
    db_url = url or os.environ.get("SABLE_DATABASE_URL")
    if not db_url:
        path = sable_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{path}"

    with _engine_lock:
        if db_url in _engine_cache:
            return _engine_cache[db_url]

        # pool_pre_ping: check a pooled connection is still alive before handing it
        # out, and transparently reconnect if not.
        #
        # Without it, every long-lived resident service (the audit bot, sable-roles,
        # sable-recon, the workflow runners) keeps connections to a Postgres process
        # that may no longer exist, and breaks until the SERVICE is restarted. Observed
        # 2026-07-29: Postgres restarted at 06:32, the audit bot had been up since the
        # previous evening, and the next audit died instantly on
        # "server closed the connection unexpectedly" — a two-hour job refused at the
        # first query, ten hours after the actual event.
        #
        # pool_recycle discards connections older than 30 min, which also covers
        # idle-timeout kills by firewalls/middleboxes between the container and the
        # host. Cheap: one lightweight round-trip per checkout of a stale connection.
        engine_kwargs: dict = {"pool_pre_ping": True}
        if not db_url.startswith("sqlite"):
            engine_kwargs["pool_recycle"] = 1800

        engine = create_engine(db_url, **engine_kwargs)

        if engine.dialect.name == "sqlite":
            _register_sqlite_pragmas(engine)
        else:
            _pin_utc_session(engine)

        _engine_cache[db_url] = engine
        return engine


def _pin_utc_session(engine: Engine) -> None:
    """Force every PostgreSQL session to UTC.

    Timestamps are stored as naive TEXT written in UTC. PostgreSQL resolves a naive string
    cast to ``timestamptz`` using the SESSION timezone, so the SAME stored value becomes a
    different instant per session. Measured on PostgreSQL 16 against ``2026-08-27 12:00:00``:

        UTC              -> 2026-08-27 12:00:00+00
        America/Los_...  -> 2026-08-27 12:00:00-07
        Asia/Tokyo       -> 2026-08-27 12:00:00+09

    A server defaulting to a non-UTC zone therefore shifts every elapsed-time check, so a
    stuck-run alert fires hours early or late. The container CI runs in ``Etc/UTC`` and hides
    this completely.
    """
    @event.listens_for(engine, "connect")
    def _set_utc(dbapi_conn, connection_record):  # noqa: ARG001
        cur = dbapi_conn.cursor()
        cur.execute("SET TIME ZONE 'UTC'")
        cur.close()


def _register_sqlite_pragmas(engine: Engine) -> None:
    """Apply the same PRAGMAs that the legacy ``get_db()`` sets."""

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, connection_record):  # noqa: ARG001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()
