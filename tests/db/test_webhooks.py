"""Tests for webhook subscription helpers."""
from __future__ import annotations

import sqlite3

import pytest

from sable_platform.db.connection import ensure_schema
from sable_platform.db.webhooks import (
    MAX_SUBSCRIPTIONS_PER_ORG,
    create_subscription,
    delete_subscription,
    list_subscriptions,
    record_failure,
    record_success,
)
from sable_platform.errors import SableError, ORG_NOT_FOUND


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def _insert_org(conn, org_id="test_org") -> str:
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, status) VALUES (?, ?, ?)",
        (org_id, "Test Org", "active"),
    )
    conn.commit()
    return org_id


_VALID_SECRET = "a" * 16
_VALID_URL = "https://example.com/webhook"


def test_create_subscription():
    conn = _make_conn()
    org_id = _insert_org(conn)
    sub_id = create_subscription(conn, org_id, _VALID_URL, ["alert.created"], _VALID_SECRET)
    assert sub_id > 0

    row = conn.execute("SELECT * FROM webhook_subscriptions WHERE id=?", (sub_id,)).fetchone()
    assert row["org_id"] == org_id
    assert row["url"] == _VALID_URL
    assert row["enabled"] == 1


def test_create_rejects_short_secret():
    conn = _make_conn()
    org_id = _insert_org(conn)
    with pytest.raises(SableError) as exc:
        create_subscription(conn, org_id, _VALID_URL, ["alert.created"], "short")
    assert "16" in str(exc.value)


def test_create_rejects_localhost_url():
    conn = _make_conn()
    org_id = _insert_org(conn)
    blocked_urls = (
        "http://localhost/hook",
        "https://127.0.0.1/hook",
        "http://10.0.0.1/hook",
        "http://192.168.1.1/hook",
        "http://172.16.0.1/hook",
        # IPv6 loopback
        "http://[::1]/hook",
        # Link-local IPv4
        "http://169.254.1.1/hook",
        # IPv6 link-local
        "http://[fe80::1]/hook",
        # 0.0.0.0
        "http://0.0.0.0/hook",
    )
    for url in blocked_urls:
        with pytest.raises(SableError):
            create_subscription(conn, org_id, url, ["alert.created"], _VALID_SECRET)


def test_create_rejects_over_max_subscriptions():
    conn = _make_conn()
    org_id = _insert_org(conn)
    for i in range(MAX_SUBSCRIPTIONS_PER_ORG):
        create_subscription(conn, org_id, f"https://example.com/hook{i}", ["alert.created"], _VALID_SECRET)

    with pytest.raises(SableError):
        create_subscription(conn, org_id, "https://example.com/hookN", ["alert.created"], _VALID_SECRET)


def test_list_subscriptions():
    conn = _make_conn()
    org_id = _insert_org(conn)
    create_subscription(conn, org_id, "https://a.com/h", ["alert.created"], _VALID_SECRET)
    create_subscription(conn, org_id, "https://b.com/h", ["workflow.completed"], _VALID_SECRET)

    rows = list_subscriptions(conn, org_id)
    assert len(rows) == 2


def test_list_subscriptions_masks_secret():
    conn = _make_conn()
    org_id = _insert_org(conn)
    create_subscription(conn, org_id, _VALID_URL, ["alert.created"], "mysecretvalue1234567")

    rows = list_subscriptions(conn, org_id)
    assert rows[0]["secret"].startswith("****")
    assert rows[0]["secret"].endswith("4567")
    assert "mysecretvalue" not in rows[0]["secret"]


def test_delete_subscription():
    conn = _make_conn()
    org_id = _insert_org(conn)
    sub_id = create_subscription(conn, org_id, _VALID_URL, ["alert.created"], _VALID_SECRET)
    assert delete_subscription(conn, sub_id) is True
    assert delete_subscription(conn, sub_id) is False


def test_record_failure_increments():
    conn = _make_conn()
    org_id = _insert_org(conn)
    sub_id = create_subscription(conn, org_id, _VALID_URL, ["alert.created"], _VALID_SECRET)

    for _ in range(3):
        record_failure(conn, sub_id, "timeout")

    row = conn.execute("SELECT consecutive_failures FROM webhook_subscriptions WHERE id=?", (sub_id,)).fetchone()
    assert row["consecutive_failures"] == 3


def test_auto_disable_after_10_failures():
    conn = _make_conn()
    org_id = _insert_org(conn)
    sub_id = create_subscription(conn, org_id, _VALID_URL, ["alert.created"], _VALID_SECRET)

    for _ in range(10):
        record_failure(conn, sub_id, "timeout")

    row = conn.execute("SELECT enabled, consecutive_failures FROM webhook_subscriptions WHERE id=?", (sub_id,)).fetchone()
    assert row["enabled"] == 0
    assert row["consecutive_failures"] == 10


def test_record_success_resets():
    conn = _make_conn()
    org_id = _insert_org(conn)
    sub_id = create_subscription(conn, org_id, _VALID_URL, ["alert.created"], _VALID_SECRET)

    for _ in range(5):
        record_failure(conn, sub_id, "timeout")

    record_success(conn, sub_id)

    row = conn.execute("SELECT consecutive_failures FROM webhook_subscriptions WHERE id=?", (sub_id,)).fetchone()
    assert row["consecutive_failures"] == 0
