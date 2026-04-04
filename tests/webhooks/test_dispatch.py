"""Tests for webhook event dispatch."""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from unittest.mock import MagicMock, patch

from sable_platform.db.connection import ensure_schema
from sable_platform.db.webhooks import create_subscription
from sable_platform.webhooks.dispatch import dispatch_event


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


_SECRET = "a" * 32
_URL = "https://example.com/webhook"


@patch("sable_platform.webhooks.dispatch.urllib.request.urlopen")
def test_dispatch_sends_to_matching_subscription(mock_urlopen, ):
    conn = _make_conn()
    org_id = _insert_org(conn)
    create_subscription(conn, org_id, _URL, ["alert.created"], _SECRET)

    count = dispatch_event(conn, "alert.created", org_id, {"test": True})
    assert count == 1
    assert mock_urlopen.call_count == 1

    req = mock_urlopen.call_args[0][0]
    assert req.full_url == _URL


@patch("sable_platform.webhooks.dispatch.urllib.request.urlopen")
def test_dispatch_skips_non_matching_event(mock_urlopen):
    conn = _make_conn()
    org_id = _insert_org(conn)
    create_subscription(conn, org_id, _URL, ["alert.created"], _SECRET)

    count = dispatch_event(conn, "workflow.completed", org_id, {"test": True})
    assert count == 0
    assert mock_urlopen.call_count == 0


@patch("sable_platform.webhooks.dispatch.urllib.request.urlopen")
def test_dispatch_includes_hmac_signature(mock_urlopen):
    conn = _make_conn()
    org_id = _insert_org(conn)
    create_subscription(conn, org_id, _URL, ["alert.created"], _SECRET)

    dispatch_event(conn, "alert.created", org_id, {"test": True})

    req = mock_urlopen.call_args[0][0]
    sig_header = req.get_header("X-sable-signature")
    assert sig_header.startswith("sha256=")

    # Verify HMAC is correct
    body_bytes = req.data
    expected = hmac.new(_SECRET.encode(), body_bytes, hashlib.sha256).hexdigest()
    assert sig_header == f"sha256={expected}"


@patch("sable_platform.webhooks.dispatch.urllib.request.urlopen", side_effect=Exception("network error"))
def test_dispatch_failure_does_not_raise(mock_urlopen):
    conn = _make_conn()
    org_id = _insert_org(conn)
    create_subscription(conn, org_id, _URL, ["alert.created"], _SECRET)

    # Should not raise
    count = dispatch_event(conn, "alert.created", org_id, {"test": True})
    assert count == 0


@patch("sable_platform.webhooks.dispatch.urllib.request.urlopen", side_effect=Exception("timeout"))
def test_dispatch_records_failure_on_error(mock_urlopen):
    conn = _make_conn()
    org_id = _insert_org(conn)
    sub_id = create_subscription(conn, org_id, _URL, ["alert.created"], _SECRET)

    dispatch_event(conn, "alert.created", org_id, {"test": True})

    row = conn.execute(
        "SELECT consecutive_failures FROM webhook_subscriptions WHERE id=?", (sub_id,)
    ).fetchone()
    assert row["consecutive_failures"] == 1


@patch("sable_platform.webhooks.dispatch.urllib.request.urlopen")
def test_dispatch_skips_disabled_subscription(mock_urlopen):
    conn = _make_conn()
    org_id = _insert_org(conn)
    sub_id = create_subscription(conn, org_id, _URL, ["alert.created"], _SECRET)

    conn.execute("UPDATE webhook_subscriptions SET enabled=0 WHERE id=?", (sub_id,))
    conn.commit()

    count = dispatch_event(conn, "alert.created", org_id, {"test": True})
    assert count == 0
    assert mock_urlopen.call_count == 0


@patch("sable_platform.webhooks.dispatch.urllib.request.urlopen")
def test_dispatch_returns_success_count(mock_urlopen):
    conn = _make_conn()
    org_id = _insert_org(conn)
    create_subscription(conn, org_id, "https://a.com/h", ["alert.created"], _SECRET)
    create_subscription(conn, org_id, "https://b.com/h", ["alert.created"], _SECRET)

    # First succeeds, second fails
    mock_urlopen.side_effect = [None, Exception("fail")]

    count = dispatch_event(conn, "alert.created", org_id, {"test": True})
    assert count == 1
