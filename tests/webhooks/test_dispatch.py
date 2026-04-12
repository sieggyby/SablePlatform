"""Tests for webhook event dispatch."""
from __future__ import annotations

import hashlib
import hmac
from unittest.mock import patch

from tests.conftest import make_test_conn
from sable_platform.db.webhooks import create_subscription
from sable_platform.webhooks.dispatch import dispatch_event, _deliver_webhook


def _make_conn():
    return make_test_conn()


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
def test_dispatch_sends_to_matching_subscription(mock_urlopen):
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

    mock_urlopen.side_effect = [None, Exception("fail")]
    count = dispatch_event(conn, "alert.created", org_id, {"test": True})
    assert count == 1


@patch("sable_platform.webhooks.dispatch.urllib.request.urlopen")
def test_dispatch_can_target_specific_subscription(mock_urlopen):
    conn = _make_conn()
    org_id = _insert_org(conn)
    sub_a = create_subscription(conn, org_id, "https://a.com/h", ["alert.created"], _SECRET)
    create_subscription(conn, org_id, "https://b.com/h", ["alert.created"], _SECRET)

    count = dispatch_event(
        conn,
        "alert.created",
        org_id,
        {"test": True},
        subscription_ids=[sub_a],
    )

    assert count == 1
    assert mock_urlopen.call_count == 1
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "https://a.com/h"


@patch("sable_platform.webhooks.dispatch.urllib.request.urlopen")
def test_dispatch_can_bypass_event_filters_for_targeted_test(mock_urlopen):
    conn = _make_conn()
    org_id = _insert_org(conn)
    sub_id = create_subscription(conn, org_id, _URL, ["alert.created"], _SECRET)

    count = dispatch_event(
        conn,
        "webhook.test",
        org_id,
        {"test": True},
        subscription_ids=[sub_id],
        bypass_event_filters=True,
    )

    assert count == 1
    assert mock_urlopen.call_count == 1


@patch("sable_platform.webhooks.dispatch.urllib.request.urlopen")
def test_deliver_webhook_computes_hmac(mock_urlopen):
    """_deliver_webhook sends correct HMAC signature."""
    conn = _make_conn()
    org_id = _insert_org(conn)
    sub_id = create_subscription(conn, org_id, _URL, ["alert.created"], _SECRET)
    body = b'{"test":true}'
    assert _deliver_webhook(conn, sub_id, _URL, _SECRET, body) is True

    req = mock_urlopen.call_args[0][0]
    sig_header = req.get_header("X-sable-signature")
    expected = hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert sig_header == f"sha256={expected}"


@patch("sable_platform.webhooks.dispatch.urllib.request.urlopen", side_effect=Exception("timeout"))
def test_deliver_webhook_failure_records_failure(mock_urlopen):
    conn = _make_conn()
    org_id = _insert_org(conn)
    sub_id = create_subscription(conn, org_id, _URL, ["alert.created"], _SECRET)

    assert _deliver_webhook(conn, sub_id, _URL, _SECRET, b"{}") is False
    row = conn.execute(
        "SELECT consecutive_failures FROM webhook_subscriptions WHERE id=?",
        (sub_id,),
    ).fetchone()
    assert row["consecutive_failures"] == 1


@patch("sable_platform.webhooks.dispatch.urllib.request.urlopen")
def test_webhook_parallel_delivery_all_succeed(mock_urlopen):
    """3 subscriptions all succeed — count=3 and all consecutive_failures reset."""
    conn = _make_conn()
    org_id = _insert_org(conn)
    create_subscription(conn, org_id, "https://a.com/h", ["alert.created"], _SECRET)
    create_subscription(conn, org_id, "https://b.com/h", ["alert.created"], _SECRET)
    create_subscription(conn, org_id, "https://c.com/h", ["alert.created"], _SECRET)

    count = dispatch_event(conn, "alert.created", org_id, {"test": True})
    assert count == 3
    assert mock_urlopen.call_count == 3


@patch("sable_platform.webhooks.dispatch.urllib.request.urlopen")
def test_webhook_partial_failure_writes_failure_row(mock_urlopen):
    """1-of-3 fails: 2 success + 1 failure row written; no exception raised."""
    conn = _make_conn()
    org_id = _insert_org(conn)
    sub_ids = [
        create_subscription(conn, org_id, f"https://sub{i}.com/h", ["alert.created"], _SECRET)
        for i in range(3)
    ]

    call_count = [0]

    def _side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 2:
            raise Exception("delivery failed")
        return None

    mock_urlopen.side_effect = _side_effect

    count = dispatch_event(conn, "alert.created", org_id, {"test": True})
    assert count == 2

    # The failing subscription must have consecutive_failures=1
    total_failures = conn.execute(
        "SELECT SUM(consecutive_failures) FROM webhook_subscriptions WHERE org_id=?",
        (org_id,),
    ).fetchone()[0]
    assert total_failures == 1
