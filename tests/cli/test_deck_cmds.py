"""CLI tests for `sable-platform deck publish-due` (the Content Deck claim-due worker, mig 077).

Invoked against a file-backed temp sable.db (resolved via ``SABLE_DB_PATH``, the same way
``main.py`` resolves the CLI target). The worker only reads/flips ``content_publish_jobs`` — no
external call — so these assert the state machine: a past-due scheduled job is flipped to 'due',
a future job is left alone, and an empty backlog reports cleanly.
"""
from __future__ import annotations

import sqlite3

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine

from sable_platform.cli.main import cli
from sable_platform.db.connection import ensure_schema
from sable_platform.db.content_deck import set_candidate_status, upsert_candidate
from sable_platform.db.content_publish import get_publish_job, schedule_candidate
from sable_platform.relay.bot.txn import immediate_txn


@pytest.fixture
def deck_db_path(tmp_path, monkeypatch):
    """A fresh file-backed sable.db wired to the CLI via SABLE_DB_PATH."""
    db_path = str(tmp_path / "deck.db")
    raw = sqlite3.connect(db_path)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys=ON")
    ensure_schema(raw)
    raw.execute("INSERT INTO orgs (org_id, display_name) VALUES ('tig', 'TIG')")
    raw.commit()
    raw.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    monkeypatch.setenv("SABLE_OPERATOR_ID", "tester")
    return db_path


def _engine(db_path: str):
    return create_engine(f"sqlite:///{db_path}")


def _seed_scheduled_job(db_path: str, *, publish_at: str, handle: str = "@tigfoundation") -> int:
    eng = _engine(db_path)
    conn = eng.connect()
    try:
        with immediate_txn(conn):
            cid = upsert_candidate(
                conn, org_id="tig", kind="meme", payload_json="{}",
                source="test", target_handle=handle,
            )
            set_candidate_status(
                conn, candidate_id=cid, org_id="tig", status="kept", expected_status="pending",
            )
            job_id = schedule_candidate(
                conn, candidate_id=cid, org_id="tig", target_handle=handle, publish_at=publish_at,
            )
        assert job_id is not None
        return job_id
    finally:
        conn.close()
        eng.dispose()


def _job_state(db_path: str, job_id: int) -> str:
    eng = _engine(db_path)
    conn = eng.connect()
    try:
        return get_publish_job(conn, job_id)["release_state"]
    finally:
        conn.close()
        eng.dispose()


def test_publish_due_flips_past_jobs_to_due(deck_db_path):
    job_id = _seed_scheduled_job(deck_db_path, publish_at="2020-01-01T00:00:00Z")
    assert _job_state(deck_db_path, job_id) == "scheduled"

    res = CliRunner().invoke(cli, ["deck", "publish-due", "--json"])
    assert res.exit_code == 0, res.output
    assert '"claimed": 1' in res.output
    assert _job_state(deck_db_path, job_id) == "due"


def test_publish_due_leaves_future_jobs(deck_db_path):
    job_id = _seed_scheduled_job(deck_db_path, publish_at="2099-01-01T00:00:00Z")

    res = CliRunner().invoke(cli, ["deck", "publish-due"])
    assert res.exit_code == 0, res.output
    assert "No due publish jobs." in res.output
    assert _job_state(deck_db_path, job_id) == "scheduled"


def test_publish_due_empty_backlog_reports_clean(deck_db_path):
    res = CliRunner().invoke(cli, ["deck", "publish-due"])
    assert res.exit_code == 0, res.output
    assert "No due publish jobs." in res.output
