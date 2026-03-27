"""Alert delivery: log + optional Telegram/Discord HTTP notification."""
from __future__ import annotations

import json as _json
import logging
import os
import sqlite3
import urllib.request

from sable_platform.db.alerts import get_last_delivered_at, mark_delivered, mark_delivery_failed

log = logging.getLogger(__name__)


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
    except Exception:
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
