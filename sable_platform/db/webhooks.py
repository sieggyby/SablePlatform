"""Webhook subscription helpers for sable.db."""
from __future__ import annotations

import ipaddress
import json
import urllib.parse

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.errors import SableError, ORG_NOT_FOUND


MAX_SUBSCRIPTIONS_PER_ORG = 5

INVALID_WEBHOOK = "INVALID_WEBHOOK"


def _is_private_url(url: str) -> bool:
    """Check if URL targets localhost, private networks, or link-local addresses.

    Uses ipaddress module to catch IPv6 loopback, hex/octal/decimal-encoded IPs,
    IPv4-mapped IPv6, and all RFC 1918/link-local ranges.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        if not host:
            return True  # no hostname = reject

        # Block 'localhost' by name (including localhost.localdomain, etc.)
        if host == "localhost" or host.endswith(".localhost"):
            return True

        # Try to parse as IP address (handles decimal, hex, octal, IPv6 forms)
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            # Not an IP literal — could be a DNS name.
            # DNS rebinding is out of scope for prefix validation.
            return False

        # Check all private/reserved ranges
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return True

        # IPv4-mapped IPv6 (::ffff:127.0.0.1)
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            mapped = addr.ipv4_mapped
            if mapped.is_private or mapped.is_loopback or mapped.is_link_local or mapped.is_reserved:
                return True

        return False
    except Exception:
        return True  # parse failure = reject


def create_subscription(
    conn: Connection,
    org_id: str,
    url: str,
    event_types: list[str],
    secret: str,
) -> int:
    """Create a webhook subscription. Returns the subscription id."""
    row = conn.execute(text("SELECT 1 FROM orgs WHERE org_id=:org_id"), {"org_id": org_id}).fetchone()
    if not row:
        raise SableError(ORG_NOT_FOUND, f"Org '{org_id}' not found")

    if len(secret) < 16:
        raise SableError(INVALID_WEBHOOK, "Secret must be at least 16 characters")

    if _is_private_url(url):
        raise SableError(INVALID_WEBHOOK, f"URL targets a private/localhost address: {url}")

    count_row = conn.execute(
        text("SELECT COUNT(*) as cnt FROM webhook_subscriptions WHERE org_id=:org_id AND enabled=1"),
        {"org_id": org_id},
    ).fetchone()
    if count_row["cnt"] >= MAX_SUBSCRIPTIONS_PER_ORG:
        raise SableError(
            INVALID_WEBHOOK,
            f"Org '{org_id}' already has {MAX_SUBSCRIPTIONS_PER_ORG} active subscriptions",
        )

    cursor = conn.execute(
        text("""
        INSERT INTO webhook_subscriptions (org_id, url, event_types, secret)
        VALUES (:org_id, :url, :event_types, :secret)
        """),
        {"org_id": org_id, "url": url, "event_types": json.dumps(event_types), "secret": secret},
    )
    conn.commit()
    return cursor.lastrowid


def list_subscriptions(conn: Connection, org_id: str) -> list[dict]:
    """List subscriptions for an org. Secrets are masked."""
    rows = conn.execute(
        text("SELECT * FROM webhook_subscriptions WHERE org_id=:org_id ORDER BY created_at"),
        {"org_id": org_id},
    ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        raw_secret = d.get("secret", "")
        d["secret"] = f"****{raw_secret[-4:]}" if len(raw_secret) >= 4 else "****"
        result.append(d)
    return result


def get_subscription(conn: Connection, subscription_id: int):
    """Get a subscription by id (raw, unmasked)."""
    return conn.execute(
        text("SELECT * FROM webhook_subscriptions WHERE id=:id"),
        {"id": subscription_id},
    ).fetchone()


def delete_subscription(conn: Connection, subscription_id: int) -> bool:
    """Delete a subscription. Returns True if deleted."""
    cursor = conn.execute(
        text("DELETE FROM webhook_subscriptions WHERE id=:id"),
        {"id": subscription_id},
    )
    conn.commit()
    return cursor.rowcount > 0


def record_failure(conn: Connection, subscription_id: int, error: str) -> None:
    """Increment failure count. Auto-disable after 10 consecutive failures."""
    conn.execute(
        text("""
        UPDATE webhook_subscriptions
        SET consecutive_failures = consecutive_failures + 1,
            last_failure_at = datetime('now'),
            last_failure_error = :error
        WHERE id=:id
        """),
        {"error": error[:500], "id": subscription_id},
    )
    # Auto-disable
    conn.execute(
        text("""
        UPDATE webhook_subscriptions
        SET enabled = 0
        WHERE id=:id AND consecutive_failures >= 10
        """),
        {"id": subscription_id},
    )
    conn.commit()


def record_success(conn: Connection, subscription_id: int) -> None:
    """Reset failure count on successful delivery."""
    conn.execute(
        text("UPDATE webhook_subscriptions SET consecutive_failures = 0 WHERE id=:id"),
        {"id": subscription_id},
    )
    conn.commit()
