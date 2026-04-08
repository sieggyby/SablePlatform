"""Tests for operator audit log helpers."""
from __future__ import annotations

import json

from sable_platform.db.audit import log_audit, list_audit_log


def test_log_audit_basic(in_memory_db):
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES (?, ?)", ("test_org", "Test"))
    conn.commit()
    row_id = log_audit(conn, "cli:alice", "alert_acknowledge", org_id="test_org")
    assert row_id > 0

    row = conn.execute("SELECT * FROM audit_log WHERE id=?", (row_id,)).fetchone()
    assert row["actor"] == "cli:alice"
    assert row["action"] == "alert_acknowledge"
    assert row["org_id"] == "test_org"
    assert row["source"] == "cli"


def test_log_audit_with_detail(in_memory_db):
    conn = in_memory_db
    detail = {"alert_id": "abc123", "reason": "investigated"}
    row_id = log_audit(conn, "op", "test_action", detail=detail)

    row = conn.execute("SELECT * FROM audit_log WHERE id=?", (row_id,)).fetchone()
    assert json.loads(row["detail_json"]) == detail


def test_list_audit_all(in_memory_db):
    conn = in_memory_db
    log_audit(conn, "a", "action1")
    log_audit(conn, "b", "action2")
    log_audit(conn, "c", "action3")

    rows = list_audit_log(conn)
    assert len(rows) == 3
    actors = {r["actor"] for r in rows}
    assert actors == {"a", "b", "c"}


def test_list_audit_filter_org(in_memory_db):
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES (?, ?)", ("org1", "O1"))
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES (?, ?)", ("org2", "O2"))
    conn.commit()
    log_audit(conn, "a", "x", org_id="org1")
    log_audit(conn, "b", "y", org_id="org2")

    rows = list_audit_log(conn, org_id="org1")
    assert len(rows) == 1
    assert rows[0]["actor"] == "a"


def test_list_audit_filter_actor(in_memory_db):
    conn = in_memory_db
    log_audit(conn, "alice", "x")
    log_audit(conn, "bob", "y")

    rows = list_audit_log(conn, actor="alice")
    assert len(rows) == 1


def test_list_audit_filter_action(in_memory_db):
    conn = in_memory_db
    log_audit(conn, "a", "alert_acknowledge")
    log_audit(conn, "b", "tag_deactivate")

    rows = list_audit_log(conn, action="tag_deactivate")
    assert len(rows) == 1
    assert rows[0]["actor"] == "b"


def test_list_audit_filter_since(in_memory_db):
    conn = in_memory_db
    conn.execute(
        "INSERT INTO audit_log (actor, action, timestamp, source) VALUES (?, ?, ?, ?)",
        ("a", "x", "2026-01-01 00:00:00", "cli"),
    )
    conn.execute(
        "INSERT INTO audit_log (actor, action, timestamp, source) VALUES (?, ?, ?, ?)",
        ("b", "y", "2026-06-01 00:00:00", "cli"),
    )
    conn.commit()

    rows = list_audit_log(conn, since="2026-03-01")
    assert len(rows) == 1
    assert rows[0]["actor"] == "b"


def test_list_audit_combined_filters(in_memory_db):
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES (?, ?)", ("org1", "O1"))
    conn.commit()
    log_audit(conn, "alice", "alert_acknowledge", org_id="org1")
    log_audit(conn, "alice", "tag_deactivate", org_id="org1")
    log_audit(conn, "bob", "alert_acknowledge", org_id="org1")

    rows = list_audit_log(conn, org_id="org1", actor="alice")
    assert len(rows) == 2
