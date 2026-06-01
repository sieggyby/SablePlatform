"""Live PostgreSQL coverage for the relay listener primitives (C2.2).

All ``tests/relay/`` tests run against in-memory SQLite only. These tests close
the production-backend gap flagged in the C2.2 fix pass: the relay reuses
SablePlatform's shared connection pool, which is LIVE Postgres on the Hetzner
VPS. They are skipped unless ``SABLE_TEST_POSTGRES_URL`` is set (see
``tests/postgres/conftest.py``).

They verify, on real Postgres:

  * ``immediate_txn`` commits, rolls back, AND keeps ``conn.in_transaction()``
    consistent across TWO sequential blocks on ONE reused connection (the
    long-lived-listener pattern) — no SA/driver tracker desync, and the
    transaction actually runs at SERIALIZABLE (PLAN §3.1 serialize-writers).
  * ``mark_processed`` first-vs-duplicate via the Postgres ``ON CONFLICT DO
    NOTHING`` + ``result.rowcount`` branch (the SQLite path uses ``changes()``).
  * The §15.3 binding lifecycle (migrate / kick) runs unchanged on Postgres —
    the helpers in ``relay/db.py`` are dialect-agnostic (named binds, ISO-Z
    timestamp param, ``rowcount`` not ``changes()``).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from sable_platform.relay.bot import binding
from sable_platform.relay.bot.dedupe import Deduper, mark_processed
from sable_platform.relay.bot.txn import immediate_txn


def _seed_org_client_binding(conn, org_id, platform, chat_id, role="operator"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id}
    )
    conn.execute(
        text(
            "INSERT INTO relay_chat_bindings (org_id, platform, chat_id, role, status) "
            "VALUES (:o, :p, :c, :r, 'active')"
        ),
        {"o": org_id, "p": platform, "c": chat_id, "r": role},
    )
    conn.commit()


# ---------------------------------------------------------------------------
# immediate_txn: commit / rollback / tracker consistency / SERIALIZABLE
# ---------------------------------------------------------------------------
def test_immediate_txn_serializable_and_tracker_consistent_on_postgres(postgres_engine) -> None:
    conn = postgres_engine.connect()
    try:
        assert conn.in_transaction() is False

        # Block 1: commit. Inside the block we must be at SERIALIZABLE.
        with immediate_txn(conn):
            level = conn.exec_driver_sql("SHOW transaction_isolation").fetchone()[0]
            assert level.lower() == "serializable"
            conn.exec_driver_sql(
                "INSERT INTO relay_processed_updates (platform, update_id) "
                "VALUES ('telegram', 'pg-c1')"
            )
        # After the manual block the SA tracker reset (no permanent desync —
        # the bug the raw-DBAPI approach had on the long-lived connection).
        assert conn.in_transaction() is False

        # Block 2 on the SAME reused connection: still SERIALIZABLE, still clean.
        with immediate_txn(conn):
            level2 = conn.exec_driver_sql("SHOW transaction_isolation").fetchone()[0]
            assert level2.lower() == "serializable"
            conn.exec_driver_sql(
                "INSERT INTO relay_processed_updates (platform, update_id) "
                "VALUES ('telegram', 'pg-c2')"
            )
        assert conn.in_transaction() is False

        count = conn.exec_driver_sql(
            "SELECT COUNT(*) FROM relay_processed_updates"
        ).fetchone()[0]
        assert count == 2
    finally:
        conn.close()


def test_immediate_txn_rollback_on_postgres(postgres_engine) -> None:
    conn = postgres_engine.connect()
    try:
        with pytest.raises(RuntimeError):
            with immediate_txn(conn):
                conn.exec_driver_sql(
                    "INSERT INTO relay_processed_updates (platform, update_id) "
                    "VALUES ('telegram', 'pg-rollback')"
                )
                raise RuntimeError("boom inside pg txn")
        assert conn.in_transaction() is False
        # The insert rolled back — a redelivery re-runs idempotently.
        count = conn.exec_driver_sql(
            "SELECT COUNT(*) FROM relay_processed_updates WHERE update_id='pg-rollback'"
        ).fetchone()[0]
        assert count == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# mark_processed: Postgres ON CONFLICT DO NOTHING + rowcount branch
# ---------------------------------------------------------------------------
def test_mark_processed_first_vs_duplicate_on_postgres(postgres_engine) -> None:
    conn = postgres_engine.connect()
    try:
        dedupe = Deduper("discord")
        snowflake = "1234567890123456789"
        with immediate_txn(conn):
            assert dedupe.claim(conn, snowflake) is True  # first insert
        with immediate_txn(conn):
            assert dedupe.claim(conn, snowflake) is False  # ON CONFLICT → rowcount 0
        # Distinct key for a different platform with the same numeric id.
        with immediate_txn(conn):
            assert mark_processed(conn, "telegram", snowflake) is True
        rows = conn.exec_driver_sql(
            "SELECT platform, update_id FROM relay_processed_updates ORDER BY platform"
        ).fetchall()
        assert ("discord", snowflake) in rows
        assert ("telegram", snowflake) in rows
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# §15.3 chat-binding lifecycle parity on Postgres
# ---------------------------------------------------------------------------
def test_migrate_chat_binding_on_postgres(postgres_engine) -> None:
    conn = postgres_engine.connect()
    try:
        _seed_org_client_binding(conn, "pgM", "telegram", "-100")
        result = binding.migrate_chat_binding(conn, "-100", "-100999")
        assert result.migrated is True
        assert result.org_id == "pgM"
        old = conn.execute(
            text("SELECT status, superseded_by_chat_id FROM relay_chat_bindings WHERE chat_id='-100'")
        ).fetchone()
        assert old[0] == "migrated"
        assert old[1] == "-100999"
        new = conn.execute(
            text("SELECT status FROM relay_chat_bindings WHERE chat_id='-100999'")
        ).fetchone()
        assert new[0] == "active"
        # The partial unique index (WHERE status='active') is honored — exactly
        # one active operator binding for the org.
        active = conn.execute(
            text(
                "SELECT COUNT(*) FROM relay_chat_bindings "
                "WHERE org_id='pgM' AND platform='telegram' AND role='operator' "
                "AND status='active'"
            )
        ).fetchone()[0]
        assert active == 1
    finally:
        conn.close()


def test_kick_chat_binding_on_postgres(postgres_engine) -> None:
    conn = postgres_engine.connect()
    try:
        _seed_org_client_binding(conn, "pgK", "telegram", "-200")
        result = binding.kick_chat_binding(conn, "-200", platform="telegram")
        assert result.flipped is True
        assert result.org_id == "pgK"
        status = conn.execute(
            text("SELECT status, last_error FROM relay_chat_bindings WHERE chat_id='-200'")
        ).fetchone()
        assert status[0] == "kicked"
        assert status[1] == "bot removed"
    finally:
        conn.close()
