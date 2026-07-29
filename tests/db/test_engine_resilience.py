"""Pooled connections must survive a database restart.

2026-07-29: Postgres restarted at 06:32; the audit bot had been running since the
previous evening. The next audit died instantly on "server closed the connection
unexpectedly" — a two-hour job refused at its first query, ten hours after the restart,
because the pool still held connections to a process that no longer existed.

Every long-lived resident service in the suite (audit bot, sable-roles, sable-recon,
workflow runners) shares this engine, so the guard belongs here rather than in any one
of them.
"""
from __future__ import annotations

import pytest

import sable_platform.db.engine as eng


def _fresh():
    eng._engine_cache.clear()


def test_sqlite_engine_pre_pings():
    _fresh()
    e = eng.get_engine("sqlite:///:memory:")
    assert e.pool._pre_ping is True


def test_postgres_engine_pre_pings_and_recycles():
    """The case that actually broke: a resident service against restartable Postgres."""
    pytest.importorskip("psycopg2", reason="Postgres dialect not installed in this env")
    _fresh()
    e = eng.get_engine("postgresql://u:p@127.0.0.1:5432/nonexistent_for_test")
    assert e.pool._pre_ping is True, "must verify liveness before handing out a connection"
    assert e.pool._recycle == 1800, "and drop connections older than 30 min"


def test_sqlite_does_not_get_a_recycle_window():
    """A local file has no middlebox to time it out; recycling is pointless churn."""
    _fresh()
    e = eng.get_engine("sqlite:///:memory:")
    assert e.pool._recycle == -1


def test_engines_are_still_cached_per_url():
    _fresh()
    a = eng.get_engine("sqlite:///:memory:")
    b = eng.get_engine("sqlite:///:memory:")
    assert a is b, "pool reuse must not regress"
