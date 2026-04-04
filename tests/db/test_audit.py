"""Tests for operator audit log helpers."""
from __future__ import annotations

import json
import sqlite3

from sable_platform.db.connection import ensure_schema
from sable_platform.db.audit import log_audit, list_audit_log


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def test_log_audit_basic():
    conn = _make_conn()
    row_id = log_audit(conn, "cli:alice", "alert_acknowledge", org_id="test_org")
    assert row_id > 0

    row = conn.execute("SELECT * FROM audit_log WHERE id=?", (row_id,)).fetchone()
    assert row["actor"] == "cli:alice"
    assert row["action"] == "alert_acknowledge"
    assert row["org_id"] == "test_org"
    assert row["source"] == "cli"


def test_log_audit_with_detail():
    conn = _make_conn()
    detail = {"alert_id": "abc123", "reason": "investigated"}
    row_id = log_audit(conn, "op", "test_action", detail=detail)

    row = conn.execute("SELECT * FROM audit_log WHERE id=?", (row_id,)).fetchone()
    assert json.loads(row["detail_json"]) == detail


def test_list_audit_all():
    conn = _make_conn()
    log_audit(conn, "a", "action1")
    log_audit(conn, "b", "action2")
    log_audit(conn, "c", "action3")

    rows = list_audit_log(conn)
    assert len(rows) == 3
    # All three present (ordering within same second is by id DESC)
    actors = {r["actor"] for r in rows}
    assert actors == {"a", "b", "c"}


def test_list_audit_filter_org():
    conn = _make_conn()
    log_audit(conn, "a", "x", org_id="org1")
    log_audit(conn, "b", "y", org_id="org2")

    rows = list_audit_log(conn, org_id="org1")
    assert len(rows) == 1
    assert rows[0]["actor"] == "a"


def test_list_audit_filter_actor():
    conn = _make_conn()
    log_audit(conn, "alice", "x")
    log_audit(conn, "bob", "y")

    rows = list_audit_log(conn, actor="alice")
    assert len(rows) == 1


def test_list_audit_filter_action():
    conn = _make_conn()
    log_audit(conn, "a", "alert_acknowledge")
    log_audit(conn, "b", "tag_deactivate")

    rows = list_audit_log(conn, action="tag_deactivate")
    assert len(rows) == 1
    assert rows[0]["actor"] == "b"


def test_list_audit_filter_since():
    conn = _make_conn()
    # Insert with explicit timestamps
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


def test_list_audit_combined_filters():
    conn = _make_conn()
    log_audit(conn, "alice", "alert_acknowledge", org_id="org1")
    log_audit(conn, "alice", "tag_deactivate", org_id="org1")
    log_audit(conn, "bob", "alert_acknowledge", org_id="org1")

    rows = list_audit_log(conn, org_id="org1", actor="alice")
    assert len(rows) == 2
