"""Migration 074 — Tweet Assist tweetbank CRUD tests.

Exercises ``sable_platform.db.tweetbank`` against the in-memory ``sa_conn`` schema:
  * submit -> list (account-scoped + global pool);
  * global (account_handle NULL) is visible to every account; an account entry is NOT;
  * fail-closed empty view (no granted accounts + global excluded -> []);
  * mark_used soft-claim (approved -> used, idempotent, used sorts last);
  * P4 pending queue + approve/reject (only a pending row flips);
  * org-scoped (orgA never leaks to orgB);
  * the no-cost-column rule + the status/source CHECK constraints.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from sable_platform.db import tweetbank as tb
from sable_platform.relay.bot.txn import immediate_txn


def _seed(conn, *, org_id="orgA"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})


def test_submit_and_list(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        eid = tb.submit_entry(
            sa_conn, client_org="orgA", text="rollups are the endgame",
            account_handle="@tigfoundation", register_band="serious",
            topic_tags=["rollups", "ip"], author="operator_arf",
        )
    assert eid > 0
    bank = tb.list_bank(sa_conn, "orgA", ["@tigfoundation"])
    assert len(bank) == 1
    e = bank[0]
    assert e["text"] == "rollups are the endgame"
    assert e["status"] == "approved" and e["source"] == "human"
    assert json.loads(e["topic_tags"]) == ["rollups", "ip"]


def test_global_pool_visible_to_every_account(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        tb.submit_entry(sa_conn, client_org="orgA", text="global gm", account_handle=None)
        tb.submit_entry(sa_conn, client_org="orgA", text="founder-only", account_handle="@Dr_JohnFletcher")
    # An operator granted only @tigfoundation sees the GLOBAL entry but NOT the founder one.
    bank = tb.list_bank(sa_conn, "orgA", ["@tigfoundation"])
    texts = {e["text"] for e in bank}
    assert "global gm" in texts
    assert "founder-only" not in texts
    # Excluding global + no matching account -> nothing.
    assert tb.list_bank(sa_conn, "orgA", ["@tigfoundation"], include_global=False) == []


def test_list_fail_closed_no_grants(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        tb.submit_entry(sa_conn, client_org="orgA", text="x", account_handle="@tigfoundation")
    # No granted accounts AND global excluded -> [] (never the whole table).
    assert tb.list_bank(sa_conn, "orgA", [], include_global=False) == []


def test_mark_used_soft_claim(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        eid = tb.submit_entry(sa_conn, client_org="orgA", text="claim me", account_handle=None)
    with immediate_txn(sa_conn):
        assert tb.mark_used(sa_conn, eid, "operator_arf") is True
    # Idempotent: a second mark-used is a no-op (already used).
    with immediate_txn(sa_conn):
        assert tb.mark_used(sa_conn, eid, "operator_ben") is False
    # 'used' still appears (advisory) but sorts last, and records who claimed it.
    bank = tb.list_bank(sa_conn, "orgA", [], statuses=("approved", "used"))
    assert bank[-1]["status"] == "used" and bank[-1]["used_by"] == "operator_arf"


def test_pending_queue_and_approve_reject(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        n = tb.add_ai_suggestions(sa_conn, client_org="orgA", entries=[
            {"text": "ai idea one", "account_handle": None, "register_band": "balanced"},
            {"text": "ai idea two", "account_handle": "@tigfoundation"},
            {"text": "   "},  # blank -> skipped
        ])
    assert n == 2
    pending = tb.list_pending(sa_conn, "orgA")
    assert {e["text"] for e in pending} == {"ai idea one", "ai idea two"}
    assert all(e["source"] == "ai" and e["status"] == "pending" for e in pending)
    # Pending entries do NOT appear in the approved bank view.
    assert tb.list_bank(sa_conn, "orgA", ["@tigfoundation"]) == []
    one, two = sorted(pending, key=lambda e: e["text"])
    with immediate_txn(sa_conn):
        assert tb.set_status(sa_conn, one["id"], "approved") is True
        assert tb.set_status(sa_conn, two["id"], "rejected") is True
    # Approved one now in the bank; rejected one nowhere visible.
    approved = tb.list_bank(sa_conn, "orgA", ["@tigfoundation"])
    assert {e["text"] for e in approved} == {"ai idea one"}
    # set_status only flips a PENDING row — re-judging an approved entry is a no-op.
    with immediate_txn(sa_conn):
        assert tb.set_status(sa_conn, one["id"], "rejected") is False


def test_org_scoped(sa_conn):
    _seed(sa_conn, org_id="orgA")
    _seed(sa_conn, org_id="orgB")
    sa_conn.commit()
    with immediate_txn(sa_conn):
        tb.submit_entry(sa_conn, client_org="orgA", text="A-only", account_handle=None)
        tb.submit_entry(sa_conn, client_org="orgB", text="B-only", account_handle=None)
    assert {e["text"] for e in tb.list_bank(sa_conn, "orgA", [])} == {"A-only"}
    assert {e["text"] for e in tb.list_bank(sa_conn, "orgB", [])} == {"B-only"}


def test_no_cost_column(sa_conn):
    rows = sa_conn.execute(text("PRAGMA table_info(tweetbank_entries)")).fetchall()
    names = {r._mapping["name"] for r in rows}
    assert not any("cost" in n.lower() for n in names), names
    assert names == {
        "id", "client_org", "account_handle", "text", "register_band", "topic_tags",
        "author", "source", "status", "created_at", "used_at", "used_by",
    }


def test_status_check_constraint(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    # The CHECK rejects a bogus status (defense-in-depth even though helpers never write one).
    with pytest.raises(IntegrityError):
        with immediate_txn(sa_conn):
            sa_conn.execute(
                text("INSERT INTO tweetbank_entries (client_org, text, status) VALUES ('orgA','x','bogus')")
            )
