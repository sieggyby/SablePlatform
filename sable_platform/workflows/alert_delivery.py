"""Alert delivery: log + optional Telegram/Discord HTTP notification."""
from __future__ import annotations

import json as _json
import logging
import os
import sqlite3
import urllib.error
import urllib.request

from sable_platform.db.alerts import get_last_delivered_at, mark_delivered, mark_delivery_failed

log = logging.getLogger(__name__)


def deliver_alerts_by_ids(
    conn: sqlite3.Connection,
    alert_ids: list[str],
) -> None:
    """Deliver alerts by their IDs. Call AFTER committing alert rows.

    Reads each alert from the DB and dispatches via _deliver().
    Delivery failures are logged but never propagated.
    """
    for alert_id in alert_ids:
        try:
            row = conn.execute(
                "SELECT org_id, severity, title, dedup_key FROM alerts WHERE alert_id=?",
                (alert_id,),
            ).fetchone()
            if not row:
                continue
            sev = row["severity"]
            org = row["org_id"]
            title = row["title"]
            msg = f"[{sev.upper()}] [{org}] {title}" if org else f"[{sev.upper()}] {title}"
            _deliver(conn, org, sev, msg, dedup_key=row["dedup_key"])
        except Exception as exc:
            log.warning("deliver_alerts_by_ids: failed for alert %s: %s", alert_id, exc)


def _deliver(
    conn: sqlite3.Connection,
    org_id: str | None,
    severity: str,
    message: str,
    *,
    dedup_key: str | None = None,
) -> None:
    """Deliver alert to configured channels (log + optional Telegram/Discord).

    If dedup_key is provided and cooldown_hours > 0, suppress delivery when
    a recent delivery already occurred within the cooldown window.
    """
    if not org_id:
        log.warning("ALERT %s: %s", severity.upper(), message)
        return

    try:
        config = conn.execute(
            "SELECT min_severity, enabled, telegram_chat_id, discord_webhook_url, cooldown_hours FROM alert_configs WHERE org_id=?",
            (org_id,),
        ).fetchone()
    except Exception as e:
        log.warning("Failed to load alert config for org %s: %s", org_id, e)
        config = None

    if config and not config["enabled"]:
        return

    severity_ranks = {"critical": 3, "warning": 2, "info": 1}
    min_sev = config["min_severity"] if config else "warning"
    if severity_ranks.get(severity, 0) < severity_ranks.get(min_sev, 2):
        return

    # Cooldown check
    if dedup_key:
        cooldown_hours = config["cooldown_hours"] if config else 4
        if cooldown_hours > 0:
            last_ts = get_last_delivered_at(conn, dedup_key)
            if last_ts:
                check = conn.execute(
                    "SELECT (julianday('now') - julianday(?)) * 24 AS hours_since",
                    (last_ts,),
                ).fetchone()
                if check and check["hours_since"] is not None and check["hours_since"] < cooldown_hours:
                    log.debug(
                        "ALERT cooldown active for dedup_key=%s (%.1f h remaining)",
                        dedup_key,
                        cooldown_hours - check["hours_since"],
                    )
                    return

    delivery_error: str | None = None

    if config and config["telegram_chat_id"]:
        token = os.environ.get("SABLE_TELEGRAM_BOT_TOKEN", "")
        if token:
            err = _send_telegram(token, config["telegram_chat_id"], message)
            if err:
                delivery_error = f"telegram: {err}"

    if config and config["discord_webhook_url"]:
        err = _send_discord(config["discord_webhook_url"], message)
        if err:
            delivery_error = delivery_error or f"discord: {err}"

    log.warning("ALERT %s [%s]: %s", severity.upper(), org_id, message)

    # Dispatch to webhooks (best-effort)
    try:
        from sable_platform.webhooks.dispatch import dispatch_event
        dispatch_event(conn, "alert.created", org_id, {
            "severity": severity,
            "title": message,
            "dedup_key": dedup_key,
        })
    except Exception as e:
        log.warning("Webhook dispatch failed during alert delivery: %s", e)

    if dedup_key:
        if delivery_error:
            mark_delivery_failed(conn, dedup_key, delivery_error)
        else:
            mark_delivered(conn, dedup_key)


def _send_telegram(token: str, chat_id: str, text: str) -> str | None:
    """Send message via Telegram. Returns error string on failure, None on success."""
    try:
        data = _json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
        return None
    except urllib.error.HTTPError as e:
        # Log only the status code — never str(e) which could include the request URL
        msg = f"HTTP {e.code}"
        log.warning("Telegram delivery failed: %s", msg)
        return msg
    except urllib.error.URLError as e:
        # Log the reason — never the full URL which contains the bot token
        msg = f"URLError: {e.reason}"
        log.warning("Telegram delivery failed: %s", msg)
        return msg
    except Exception as e:
        log.warning("Telegram delivery failed: %s", e)
        return str(e)


def _send_discord(webhook_url: str, text: str) -> str | None:
    """Send message via Discord webhook. Returns error string on failure, None on success."""
    try:
        data = _json.dumps({"content": text}).encode()
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
        return None
    except Exception as e:
        log.warning("Discord delivery failed: %s", e)
        return str(e)
