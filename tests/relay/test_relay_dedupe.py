"""C2.2 dedupe + BEGIN IMMEDIATE primitive tests.

Covers PLAN §3.1 step 1 / §3.3:
  - duplicate update_id is dropped (changes()==0 path)
  - the dedupe is PERSISTENT and restart-safe (survives a new connection to
    the same file-backed db)
  - the dedupe insert is atomic with the rest of the handler txn (rollback on
    exception inside BEGIN IMMEDIATE rolls the dedupe row back too — so a crash
    re-runs idempotently)
  - the txn helper commits on clean exit
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, text

from sable_platform.db.schema import metadata as sa_metadata
from sable_platform.relay.bot.dedupe import Deduper, mark_processed
from sable_platform.relay.bot.txn import immediate_txn


def _file_engine(db_file):
    engine = create_engine(f"sqlite:///{db_file}")

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # noqa: ARG001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def _processed_rows(conn):
    return conn.exec_driver_sql(
        "SELECT platform, update_id FROM relay_processed_updates ORDER BY update_id"
    ).fetchall()


# ---------------------------------------------------------------------------
# Basic dedupe semantics (in-memory)
# ---------------------------------------------------------------------------
def test_first_update_is_claimed(sa_conn) -> None:
    with immediate_txn(sa_conn):
        assert mark_processed(sa_conn, "telegram", 100) is True
    assert _processed_rows(sa_conn) == [("telegram", "100")]


def test_duplicate_update_id_is_dropped(sa_conn) -> None:
    dedupe = Deduper("telegram")
    with immediate_txn(sa_conn):
        assert dedupe.claim(sa_conn, 42) is True
    # Second delivery of the SAME update_id: dropped.
    with immediate_txn(sa_conn):
        assert dedupe.claim(sa_conn, 42) is False
    # Only one row exists.
    assert _processed_rows(sa_conn) == [("telegram", "42")]


def test_same_id_different_platform_is_independent(sa_conn) -> None:
    with immediate_txn(sa_conn):
        assert mark_processed(sa_conn, "telegram", 7) is True
    with immediate_txn(sa_conn):
        # Discord update with the same numeric id is a DIFFERENT key.
        assert mark_processed(sa_conn, "discord", 7) is True
    assert _processed_rows(sa_conn) == [("discord", "7"), ("telegram", "7")]


def test_discord_snowflake_id_stored_as_text(sa_conn) -> None:
    # A Discord interaction snowflake overflows signed-64 INTEGER; stored as TEXT.
    snowflake = "1234567890123456789"
    with immediate_txn(sa_conn):
        assert mark_processed(sa_conn, "discord", snowflake) is True
    with immediate_txn(sa_conn):
        assert mark_processed(sa_conn, "discord", snowflake) is False


def test_unknown_platform_rejected(sa_conn) -> None:
    with pytest.raises(ValueError):
        with immediate_txn(sa_conn):
            mark_processed(sa_conn, "matrix", 1)
    with pytest.raises(ValueError):
        Deduper("irc")


# ---------------------------------------------------------------------------
# Restart-safe / persistent (file-backed db, fresh connection = "restart")
# ---------------------------------------------------------------------------
def test_dedupe_is_restart_safe(tmp_path) -> None:
    db_file = tmp_path / "relay.db"
    engine = _file_engine(db_file)
    sa_metadata.create_all(engine)

    # "Process 1": claim update 555, commit, dispose the connection.
    with engine.connect() as conn:
        with immediate_txn(conn):
            assert mark_processed(conn, "telegram", 555) is True
    engine.dispose()

    # "Process 2" (restart): brand-new engine + connection to the SAME file.
    engine2 = _file_engine(db_file)
    with engine2.connect() as conn2:
        with immediate_txn(conn2):
            # The redelivered update_id is dropped — the row PERSISTED to disk.
            assert mark_processed(conn2, "telegram", 555) is False
        assert _processed_rows(conn2) == [("telegram", "555")]
    engine2.dispose()


# ---------------------------------------------------------------------------
# Atomicity: dedupe insert rolls back with the rest of the handler txn
# ---------------------------------------------------------------------------
def test_dedupe_insert_rolls_back_on_handler_exception(sa_conn) -> None:
    # Simulate a handler crash AFTER claiming dedupe but BEFORE commit.
    with pytest.raises(RuntimeError):
        with immediate_txn(sa_conn):
            assert mark_processed(sa_conn, "telegram", 909) is True
            raise RuntimeError("handler blew up mid-transaction")
    # The dedupe row was rolled back — so a redelivery re-runs idempotently.
    assert _processed_rows(sa_conn) == []
    with immediate_txn(sa_conn):
        assert mark_processed(sa_conn, "telegram", 909) is True
    assert _processed_rows(sa_conn) == [("telegram", "909")]


def test_immediate_txn_commits_on_clean_exit(sa_conn) -> None:
    with immediate_txn(sa_conn):
        sa_conn.exec_driver_sql(
            "INSERT INTO relay_processed_updates (platform, update_id) "
            "VALUES ('telegram', 'committed')"
        )
    # New read on the same connection sees the committed row.
    assert ("telegram", "committed") in _processed_rows(sa_conn)
