"""Webhook event dispatch for sable.db."""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import logging
import sqlite3
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from sable_platform.db.webhooks import record_failure, record_success

log = logging.getLogger(__name__)

_MAX_WORKERS = 4


def _http_deliver(url: str, secret: str, body_bytes: bytes) -> tuple[bool, str]:
    """Perform the HTTP POST for one webhook delivery. No DB access.

    Returns (success, error_message). error_message is empty string on success.
    """
    try:
        sig = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Sable-Signature": f"sha256={sig}",
            },
        )
        urllib.request.urlopen(req, timeout=1)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _deliver_webhook(
    conn,
    sub_id: int,
    url: str,
    secret: str,
    body_bytes: bytes,
) -> bool:
    """Deliver a single webhook synchronously using the caller's DB connection.

    Kept for backward compatibility with existing tests and direct callers.
    """
    ok, err = _http_deliver(url, secret, body_bytes)
    if ok:
        record_success(conn, sub_id)
        return True
    log.warning("Webhook delivery failed for subscription %d: %s", sub_id, err)
    try:
        record_failure(conn, sub_id, err)
    except Exception:
        log.error("Failed to record webhook failure for subscription %d", sub_id)
    return False


def dispatch_event(
    conn: sqlite3.Connection,
    event_type: str,
    org_id: str,
    payload: dict,
    *,
    subscription_ids: list[int] | None = None,
    bypass_event_filters: bool = False,
) -> int:
    """Dispatch event to matching webhook subscriptions.

    HTTP deliveries are fanned out in parallel (max _MAX_WORKERS threads).
    DB writes happen serially on the caller's connection after all futures complete.

    Returns the number of successful deliveries.
    """
    rows = conn.execute(
        "SELECT * FROM webhook_subscriptions WHERE org_id=? AND enabled=1",
        (org_id,),
    ).fetchall()
    if subscription_ids is not None:
        allowed_ids = set(subscription_ids)
        rows = [row for row in rows if row["id"] in allowed_ids]

    # Build (sub_id, url, secret, body_bytes) for each matching subscription.
    deliveries: list[tuple[int, str, str, bytes]] = []
    for row in rows:
        sub_events = json.loads(row["event_types"])
        if not bypass_event_filters and event_type not in sub_events:
            continue
        body = {
            "event_type": event_type,
            "org_id": org_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "payload": payload,
        }
        body_bytes = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
        deliveries.append((row["id"], row["url"], row["secret"], body_bytes))

    if not deliveries:
        return 0

    # Fan out HTTP calls in parallel.
    results: dict[int, tuple[bool, str]] = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        future_to_sub = {
            executor.submit(_http_deliver, url, secret, body_bytes): sub_id
            for sub_id, url, secret, body_bytes in deliveries
        }
        for future in as_completed(future_to_sub):
            sub_id = future_to_sub[future]
            try:
                ok, err = future.result()
            except Exception as exc:
                ok, err = False, str(exc)
            results[sub_id] = (ok, err)

    # Write success/failure to DB serially on the caller's connection.
    delivered = 0
    for sub_id, url, secret, body_bytes in deliveries:
        ok, err = results[sub_id]
        if ok:
            record_success(conn, sub_id)
            delivered += 1
        else:
            log.warning("Webhook delivery failed for subscription %d: %s", sub_id, err)
            try:
                record_failure(conn, sub_id, err)
            except Exception:
                log.error("Failed to record webhook failure for subscription %d", sub_id)

    return delivered
