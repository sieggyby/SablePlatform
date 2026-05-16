"""Tests for db/alerts.py ack/resolve refactor (API audit findings):

  - ack/resolve are now idempotent (no duplicate audit rows on re-call)
  - audit_log source/actor can be set explicitly (no env fallback for API)
  - detail_extra (token_id) lands in audit_log.detail_json
"""
from __future__ import annotations

import json

from sqlalchemy import text

from sable_platform.db.alerts import (
    acknowledge_alert,
    create_alert,
    get_alert,
    resolve_alert,
)


def test_acknowledge_alert_idempotent_no_dup_audit(org_db):
    conn, org_id = org_db
    aid = create_alert(conn, "test_type", "warning", "title", org_id=org_id)

    status1 = acknowledge_alert(conn, aid, "alice")
    status2 = acknowledge_alert(conn, aid, "alice")

    assert status1 == "acknowledged"
    assert status2 == "already_acknowledged"

    n = conn.execute(
        text("SELECT COUNT(*) FROM audit_log WHERE action='alert_acknowledge'")
    ).fetchone()[0]
    assert n == 1, "second ack should not write a second audit row"


def test_acknowledge_alert_after_resolve_is_noop(org_db):
    conn, org_id = org_db
    aid = create_alert(conn, "t", "warning", "title", org_id=org_id)
    resolve_alert(conn, aid)
    status = acknowledge_alert(conn, aid, "alice")
    assert status == "already_resolved"


def test_acknowledge_alert_missing_returns_not_found(org_db):
    conn, _ = org_db
    assert acknowledge_alert(conn, "nope", "alice") == "not_found"


def test_resolve_alert_idempotent_no_dup_audit(org_db):
    conn, org_id = org_db
    aid = create_alert(conn, "t", "warning", "title", org_id=org_id)

    s1 = resolve_alert(conn, aid)
    s2 = resolve_alert(conn, aid)

    assert s1 == "resolved"
    assert s2 == "already_resolved"

    n = conn.execute(
        text("SELECT COUNT(*) FROM audit_log WHERE action='alert_resolve'")
    ).fetchone()[0]
    assert n == 1


def test_resolve_alert_missing_returns_not_found(org_db):
    conn, _ = org_db
    assert resolve_alert(conn, "nope") == "not_found"


def test_acknowledge_alert_records_token_in_audit(org_db):
    conn, org_id = org_db
    aid = create_alert(conn, "t", "warning", "title", org_id=org_id)

    acknowledge_alert(
        conn, aid, "operator_via_api",
        source="api",
        detail_extra={"token_id": "sp_live_abcdefgh"},
    )

    row = conn.execute(
        text("SELECT actor, source, detail_json FROM audit_log"
             " WHERE action='alert_acknowledge'")
    ).fetchone()
    assert row["actor"] == "operator_via_api"
    assert row["source"] == "api"
    detail = json.loads(row["detail_json"])
    assert detail["alert_id"] == aid
    assert detail["token_id"] == "sp_live_abcdefgh"


def test_resolve_alert_records_token_in_audit(org_db):
    conn, org_id = org_db
    aid = create_alert(conn, "t", "warning", "title", org_id=org_id)

    resolve_alert(
        conn, aid, actor="api_operator", source="api",
        detail_extra={"token_id": "sp_live_zzz"},
    )
    row = conn.execute(
        text("SELECT actor, source, detail_json FROM audit_log"
             " WHERE action='alert_resolve'")
    ).fetchone()
    assert row["actor"] == "api_operator"
    assert row["source"] == "api"
    detail = json.loads(row["detail_json"])
    assert detail["token_id"] == "sp_live_zzz"


def test_get_alert_returns_row(org_db):
    conn, org_id = org_db
    aid = create_alert(conn, "t", "warning", "title", org_id=org_id)
    row = get_alert(conn, aid)
    assert row is not None
    assert row["alert_id"] == aid
    assert row["org_id"] == org_id


def test_get_alert_returns_none_for_missing(org_db):
    conn, _ = org_db
    assert get_alert(conn, "no_such") is None
