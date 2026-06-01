"""The ``BEGIN IMMEDIATE`` transaction helper (SableRelay PLAN §3.1).

Every listener side effect — dedupe insert, quorum state transition + outbox
enqueue, chat-binding lifecycle transition — runs inside **one**
``BEGIN IMMEDIATE`` transaction. This module is the single home for that
primitive so the load-bearing invariant is enforced in one place:

    **No external API call (Telegram / Discord / SocialData / HTTP) happens
    inside a ``BEGIN IMMEDIATE`` transaction.** (PLAN §3.1 line 145, the
    audit/exit criterion for C2.2.)

On SQLite, ``BEGIN IMMEDIATE`` takes the database's RESERVED write lock at
statement time (rather than lazily at first write like plain ``BEGIN``), so
concurrent writers serialize deterministically — this is what makes the
quorum guarded-UPDATE exactly-once (C2.3a) and the dedupe insert atomic.

On Postgres (the shared SP pool may be Postgres on the VPS), ``BEGIN
IMMEDIATE`` is not valid syntax; we run the block inside a ``SERIALIZABLE``
SQLAlchemy transaction instead, which gives the equivalent serialize-writers
guarantee.

Implementation note: the helper drives the transaction **through SQLAlchemy**
on BOTH backends so the SA transaction tracker and the driver-level transaction
can never desync (the earlier raw-DBAPI ``BEGIN IMMEDIATE`` / ``raw.commit()``
approach left ``conn.in_transaction()`` permanently stuck ``True`` on the
long-lived listener connection, and on Postgres a raw ``BEGIN ISOLATION LEVEL
SERIALIZABLE`` collided with psycopg2's implicit transaction — silently
downgrading the serialize-writers guarantee).

  * **SQLite** — we issue the explicit ``BEGIN IMMEDIATE`` via
    ``conn.exec_driver_sql`` (NOT the raw cursor), so SQLAlchemy's autobegin
    tracks it; the RESERVED write lock is still taken at statement time. The
    block is committed/rolled back with SA-level ``conn.commit()`` /
    ``conn.rollback()``, keeping ``conn.in_transaction()`` honest.
  * **Postgres** — ``conn.execution_options(isolation_level='SERIALIZABLE')``
    pins the level for this transaction and ``with conn.begin():`` owns the
    BEGIN/COMMIT/ROLLBACK, so SA and psycopg2 share one transaction manager.

Contract: the connection handed to ``immediate_txn`` owns its transaction
boundary exclusively. Read helpers commonly run a ``SELECT`` just before the
txn (e.g. ``binding._kicked_after_threshold``), and under SA 2.0 autobegin that
SELECT leaves a read transaction open. So at entry we ``rollback()`` any
already-open SQLAlchemy transaction to start from a clean slate: a read-only
autobegin rollback discards nothing durable, and it guarantees the explicit
``BEGIN IMMEDIATE`` / ``conn.begin()`` below never collides with a stale
transaction (the old raw-DBAPI path would otherwise hit the classic SQLite
"cannot start a transaction within a transaction"). This also protects the
C2.3a quorum tally / C2.4 publisher that share this long-lived listener
connection: no stale autobegin leaks across reaction boundaries.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


@contextmanager
def immediate_txn(conn: Connection) -> Iterator[Connection]:
    """Run a block inside one ``BEGIN IMMEDIATE`` (SQLite) / serializable txn.

    Usage::

        with immediate_txn(conn):
            conn.exec_driver_sql("INSERT OR IGNORE INTO relay_processed_updates ...")
            ...  # all DB work, NO external API call
        # COMMIT happens on clean exit; ROLLBACK on exception.

    The yielded object is the same SQLAlchemy ``Connection`` (so callers can use
    ``conn.exec_driver_sql`` / ``conn.execute`` inside the block). The
    transaction is driven entirely through SQLAlchemy so the SA tracker and the
    driver transaction stay consistent (``conn.in_transaction()`` is ``False``
    again after exit, on every call).

    The transaction is committed on normal exit and rolled back on any
    exception — so a crash inside the block rolls back the dedupe insert too,
    making the next redelivery of the same ``update_id`` re-run idempotently
    (PLAN §3.1 "Crash window").

    If ``conn`` already has an open SQLAlchemy transaction (typically a
    read-only autobegin left by a ``SELECT`` run just before this call), it is
    rolled back first so the immediate transaction starts from a clean slate
    (the listener owns the connection's transaction boundary exclusively).
    """
    if conn.in_transaction():
        # Clear a stale autobegin (usually a read SELECT) so the explicit
        # BEGIN IMMEDIATE / conn.begin() below cannot collide with it. A
        # read-only rollback discards nothing durable.
        conn.rollback()
    dialect = conn.dialect.name
    if dialect == "sqlite":
        # Explicit BEGIN IMMEDIATE through SA so autobegin tracks it; the
        # RESERVED write lock is still taken at statement time (serialized
        # writers), and SA-level commit/rollback keep the tracker honest.
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            try:
                conn.rollback()
            except Exception:  # pragma: no cover - rollback best-effort
                logger.exception("relay immediate_txn rollback failed")
            raise
        else:
            conn.commit()
    else:
        # Postgres (and any non-sqlite backend): serialize writers via a
        # SERIALIZABLE SQLAlchemy transaction. conn.begin() owns BEGIN/COMMIT/
        # ROLLBACK so SA and the driver never desync.
        pinned = conn.execution_options(isolation_level="SERIALIZABLE")
        with pinned.begin():
            yield pinned
