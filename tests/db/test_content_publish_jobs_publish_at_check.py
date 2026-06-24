"""DB-level strict-UTC CHECK on content_publish_jobs.publish_at (migration 077, Codex Tier-2).

The claim-due worker compares publish_at LEXICALLY, so a non-canonical value (offset/naive/space/
compact/fractional) would release early or never release. The Slopper route + schedule_candidate()
validate it, but a DIRECT writer/backfill must not be able to store a malformed instant. These tests
prove a RAW insert (bypassing the accessor) of a bad publish_at is rejected by the DB CHECK on BOTH
schema paths (the SA metadata.create_all path AND the legacy .sql ensure_schema migration path), and
that the canonical form is accepted.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from tests.conftest import make_test_conn, make_test_file_db

GOOD = "2099-01-01T14:00:00Z"
BADS = [
    "2099-01-01T14:00:00+02:00",  # offset zone (not UTC) -> compares wrong lexically
    "2099-01-01T14:00:00-05:00",  # negative offset
    "2099-01-01T14:00:00",         # naive (no zone) -> sorts before any 'Z' value
    "2099-01-01 14:00:00Z",        # space separator, not 'T'
    "20990101T140000Z",            # compact form (not the canonical shape)
    "2099-01-01T14:00:00.5Z",      # fractional seconds (would skew the lexical compare)
    "20X9-01-01T14:00:00Z",        # non-digit in a digit position
    "",                             # empty
]


def _seed_candidate(conn) -> int:
    from sable_platform.relay.bot.txn import immediate_txn
    from sable_platform.db import content_deck as cd
    sa_conn = getattr(conn, "_conn", conn)
    with immediate_txn(sa_conn):
        return cd.upsert_candidate(
            conn, org_id="tig", kind="meme", source="test", target_handle="@tigfoundation",
            payload_json='{"template_id":"drake"}',
        )


def _raw_insert(conn, cid: int, publish_at: str) -> None:
    """A RAW insert that BYPASSES schedule_candidate's Python validation (the direct-writer threat)."""
    conn.execute(
        "INSERT INTO content_publish_jobs (candidate_id, org_id, target_handle, publish_at) "
        "VALUES (?, ?, ?, ?)",
        (cid, "tig", "@tigfoundation", publish_at),
    )
    conn.commit()


def _check_path(conn):
    cid = _seed_candidate(conn)
    # The canonical strict-UTC form is accepted.
    _raw_insert(conn, cid, GOOD)
    n = conn.execute("SELECT COUNT(*) AS n FROM content_publish_jobs").fetchone()[0]
    assert int(n) == 1
    # Every malformed value is rejected by the DB CHECK (IntegrityError), never stored.
    for bad in BADS:
        with pytest.raises(IntegrityError):
            _raw_insert(conn, cid, bad)
        conn.rollback()  # clear the aborted txn before the next attempt
    n = conn.execute("SELECT COUNT(*) AS n FROM content_publish_jobs").fetchone()[0]
    assert int(n) == 1  # only the GOOD row persisted


def test_sa_metadata_path_rejects_bad_publish_at():
    conn = make_test_conn(with_org="tig")
    try:
        _check_path(conn)
    finally:
        conn.close()


def test_legacy_migration_path_rejects_bad_publish_at(tmp_path):
    conn = make_test_file_db(str(tmp_path / "sable.db"), with_org="tig")
    try:
        _check_path(conn)
    finally:
        conn.close()
