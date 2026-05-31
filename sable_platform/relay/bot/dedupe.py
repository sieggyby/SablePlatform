"""Persistent, restart-safe update dedupe (PLAN §3.1 step 1 / §3.3).

Both transports redeliver: Telegram long-polling re-sends an ``update_id`` if
the offset wasn't acknowledged before a crash; Discord redelivers gateway
events / interactions on reconnect. The relay must process each update
**exactly once** even across a process restart. The dedupe gate is therefore
backed by the persistent ``relay_processed_updates`` table (NOT an in-memory
set), keyed ``(platform, update_id)`` — its PRIMARY KEY.

The gate is the FIRST thing inside the listener's ``BEGIN IMMEDIATE``
(PLAN §3.1 step 1): ``INSERT OR IGNORE`` then check ``changes()``. If
``changes() == 0`` the update was already processed — the handler returns
without doing any work. Because the insert is atomic with the rest of the
handler's DB work in the same transaction, a crash before ``COMMIT`` rolls the
dedupe row back too, so redelivery re-runs idempotently (PLAN §3.1
"Crash window"). After ``COMMIT`` the row persists, so redelivery after a
restart is dropped.

GC of rows older than 7 days is owned by the C2.4 sweeper (PLAN §15.5 /
the ``relay_processed_updates_gc`` index); this module only writes.
"""
from __future__ import annotations

import logging

from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

PLATFORMS = ("telegram", "discord")


def _normalize_update_id(update_id: object) -> str:
    """Coerce an update id to the TEXT shape ``relay_processed_updates`` stores.

    Telegram ``update_id`` is an int; Discord interaction/event ids are large
    snowflake ints (often passed as ``str``). The column is TEXT (so it holds
    both Discord snowflakes that overflow SQLite's signed-64 INTEGER and TG
    ints uniformly), so everything is stringified at the boundary.
    """
    return str(update_id)


def mark_processed(conn: Connection, platform: str, update_id: object) -> bool:
    """Atomically claim ``(platform, update_id)`` as processed.

    MUST be called inside an open ``BEGIN IMMEDIATE`` transaction (it does NOT
    open or commit its own — the caller owns the transaction so the dedupe
    insert is atomic with the rest of the handler's DB work, per §3.1).

    Returns ``True`` if this is the FIRST time we have seen the update (the
    caller should proceed to process it), ``False`` if it was already processed
    (the caller should return without side effects). Implemented as
    ``INSERT OR IGNORE`` + ``changes()`` (SQLite) / ``ON CONFLICT DO NOTHING
    RETURNING`` semantics via ``cur.rowcount`` (Postgres).
    """
    if platform not in PLATFORMS:
        raise ValueError(
            f"unknown relay platform {platform!r}; expected one of {PLATFORMS}"
        )
    uid = _normalize_update_id(update_id)
    dialect = conn.dialect.name
    if dialect == "sqlite":
        conn.exec_driver_sql(
            "INSERT OR IGNORE INTO relay_processed_updates "
            "(platform, update_id) VALUES (?, ?)",
            (platform, uid),
        )
        changed = conn.exec_driver_sql("SELECT changes()").fetchone()[0]
        return bool(changed)
    # Postgres: ON CONFLICT DO NOTHING; rowcount reflects whether a row was
    # actually inserted.
    result = conn.exec_driver_sql(
        "INSERT INTO relay_processed_updates (platform, update_id) "
        "VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (platform, uid),
    )
    return bool(result.rowcount)


class Deduper:
    """Thin platform-bound facade over :func:`mark_processed`.

    The listener constructs one per transport so call sites read
    ``self._dedupe.claim(conn, update_id)`` rather than threading the platform
    string everywhere. Holds no state — the persistence lives entirely in
    ``relay_processed_updates``, which is what makes it restart-safe.
    """

    def __init__(self, platform: str) -> None:
        if platform not in PLATFORMS:
            raise ValueError(
                f"unknown relay platform {platform!r}; expected one of {PLATFORMS}"
            )
        self.platform = platform

    def claim(self, conn: Connection, update_id: object) -> bool:
        """Return True iff this update is new (proceed), False if duplicate."""
        return mark_processed(conn, self.platform, update_id)
