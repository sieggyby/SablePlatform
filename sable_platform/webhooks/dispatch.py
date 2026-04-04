"""Webhook event dispatch for sable.db."""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import logging
import sqlite3
import urllib.request

from sable_platform.db.webhooks import get_subscription, record_failure, record_success

log = logging.getLogger(__name__)


def dispatch_event(
    conn: sqlite3.Connection,
    event_type: str,
    org_id: str,
    payload: dict,
) -> int:
    """Dispatch event to matching webhook subscriptions. Returns success count."""
    rows = conn.execute(
        "SELECT * FROM webhook_subscriptions WHERE org_id=? AND enabled=1",
        (org_id,),
    ).fetchall()

    success_count = 0
    for row in rows:
        sub_events = json.loads(row["event_types"])
        if event_type not in sub_events:
            continue

        try:
            body = {
                "event_type": event_type,
                "org_id": org_id,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "payload": payload,
            }
            body_bytes = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()

            sig = hmac.new(
                row["secret"].encode(),
                body_bytes,
                hashlib.sha256,
            ).hexdigest()

            req = urllib.request.Request(
                row["url"],
                data=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Sable-Signature": f"sha256={sig}",
                },
            )
            urllib.request.urlopen(req, timeout=3)
            record_success(conn, row["id"])
            success_count += 1
        except Exception as exc:
            log.warning("Webhook delivery failed for subscription %d: %s", row["id"], exc)
            record_failure(conn, row["id"], str(exc))

    return success_count
