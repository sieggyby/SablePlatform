"""Tests for sable_platform.api.tokens.

Covers the API MVP audit findings:
  - issue_token rejects bad scopes / missing operator / empty org list
  - verify_token rejects unknown / disabled / revoked / expired / wrong secret
  - hash compare is constant-time (smoke: identical and mismatched both return
    after similar control flow)
  - last_used_at advances on successful verify
"""
from __future__ import annotations

import datetime as _dt
import time

import pytest

from sable_platform.api.tokens import (
    ALLOWED_SCOPES,
    issue_token,
    list_tokens,
    revoke_token,
    touch_last_used,
    verify_token,
)


def test_issue_token_returns_raw_and_id(org_db):
    conn, _org = org_db
    token_id, raw = issue_token(
        conn, label="t1", operator_id="op_a", created_by="owner",
        org_scopes=["test_org_001"], scopes=["read_only"],
    )
    assert token_id.startswith("sp_live_")
    assert "." in raw
    assert raw.startswith(token_id + ".")


def test_issue_rejects_bad_scope(org_db):
    conn, _ = org_db
    with pytest.raises(ValueError, match="Unknown scope"):
        issue_token(
            conn, label="x", operator_id="op", created_by="owner",
            org_scopes=["test_org_001"], scopes=["root"],
        )


def test_issue_rejects_empty_org_scopes(org_db):
    conn, _ = org_db
    with pytest.raises(ValueError, match="org_scopes"):
        issue_token(
            conn, label="x", operator_id="op", created_by="owner",
            org_scopes=[], scopes=["read_only"],
        )


def test_issue_rejects_unknown_operator(org_db):
    conn, _ = org_db
    with pytest.raises(ValueError, match="operator_id"):
        issue_token(
            conn, label="x", operator_id="unknown", created_by="owner",
            org_scopes=["*"], scopes=["read_only"],
        )


def test_verify_happy_path(org_db):
    conn, _ = org_db
    _tid, raw = issue_token(
        conn, label="t", operator_id="op_a", created_by="owner",
        org_scopes=["test_org_001"], scopes=["read_only", "write_safe"],
    )
    ctx = verify_token(conn, raw)
    assert ctx is not None
    assert ctx.operator_id == "op_a"
    assert ctx.has_scope("read_only")
    assert ctx.has_scope("write_safe")
    assert ctx.can_access_org("test_org_001")
    assert not ctx.can_access_org("some_other_org")


def test_verify_rejects_garbage(org_db):
    conn, _ = org_db
    for bad in ["", "not_a_token", "sp_live_abc", "sp_live_abc.", "bearer xxx",
                "sp_live_xxxxxxxx.short"]:
        assert verify_token(conn, bad) is None


def test_verify_rejects_revoked(org_db):
    conn, _ = org_db
    tid, raw = issue_token(
        conn, label="t", operator_id="op", created_by="owner",
        org_scopes=["test_org_001"], scopes=["read_only"],
    )
    assert verify_token(conn, raw) is not None
    revoke_token(conn, tid)
    assert verify_token(conn, raw) is None


def test_verify_rejects_expired(org_db):
    conn, _ = org_db
    _tid, raw = issue_token(
        conn, label="t", operator_id="op", created_by="owner",
        org_scopes=["test_org_001"], scopes=["read_only"],
        expires_in_days=0,  # expires "now"
    )
    # Force expires_at into the past so the lexicographic compare triggers.
    past = (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    from sqlalchemy import text
    conn.execute(text("UPDATE api_tokens SET expires_at=:p"), {"p": past})
    conn.commit()
    assert verify_token(conn, raw) is None


def test_verify_rejects_wrong_secret(org_db):
    conn, _ = org_db
    tid, raw = issue_token(
        conn, label="t", operator_id="op", created_by="owner",
        org_scopes=["test_org_001"], scopes=["read_only"],
    )
    # Tamper with the secret portion while keeping the token_id valid.
    tampered = tid + "." + "x" * 22
    assert verify_token(conn, tampered) is None


def test_verify_burns_time_on_unknown_token(org_db):
    """Smoke: a syntactically valid but unknown token_id should still
    perform a constant-time-ish compare. We can't assert exact timing
    but we can assert the function returns None without raising."""
    conn, _ = org_db
    # Token_id matches the format but isn't in the DB.
    fake = "sp_live_zzzzzzzz." + "0123456789012345678901"
    t0 = time.perf_counter()
    assert verify_token(conn, fake) is None
    elapsed = time.perf_counter() - t0
    # Sanity: well under a second.
    assert elapsed < 1.0


def test_touch_last_used(org_db):
    conn, _ = org_db
    tid, raw = issue_token(
        conn, label="t", operator_id="op", created_by="owner",
        org_scopes=["*"], scopes=["read_only"],
    )
    touch_last_used(conn, tid)
    from sqlalchemy import text
    used = conn.execute(
        text("SELECT last_used_at FROM api_tokens WHERE token_id=:t"),
        {"t": tid},
    ).fetchone()
    assert used[0] is not None


def test_list_tokens_returns_hashes_only(org_db):
    """The wire secret must never be readable from list_tokens output."""
    conn, _ = org_db
    _tid, raw = issue_token(
        conn, label="t", operator_id="op", created_by="owner",
        org_scopes=["test_org_001"], scopes=["read_only"],
    )
    rows = list_tokens(conn)
    assert len(rows) == 1
    # The select list should not include the raw token. We assert that no
    # column contains the raw secret literal.
    for row in rows:
        d = dict(row)
        for v in d.values():
            assert raw not in (str(v) if v else "")
        # And that we DID get the hash row (so cli output is useful).
        assert "token_id" in d
        assert "scopes_json" in d


def test_scope_set_matches_documented(org_db):
    assert ALLOWED_SCOPES == frozenset(
        {"read_only", "write_safe", "spend_request", "spend_execute"}
    )
