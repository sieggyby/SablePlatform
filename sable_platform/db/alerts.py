"""Alert and alert config helpers for sable.db."""
from __future__ import annotations

import sqlite3
import uuid

_SEVERITY_RANK = {"critical": 3, "warning": 2, "info": 1}


def upsert_alert_config(
    conn: sqlite3.Connection,
    org_id: str,
    *,
    min_severity: str = "warning",
    telegram_chat_id: str | None = None,
    discord_webhook_url: str | None = None,
    enabled: bool = True,
) -> str:
    """Create or update the alert config for an org. Returns config_id."""
    existing = conn.execute(
        "SELECT config_id FROM alert_configs WHERE org_id=?", (org_id,)
    ).fetchone()
    if existing:
        config_id = existing["config_id"]
        conn.execute(
            """
            UPDATE alert_configs
            SET min_severity=?, telegram_chat_id=?, discord_webhook_url=?, enabled=?
            WHERE config_id=?
            """,
            (min_severity, telegram_chat_id, discord_webhook_url, int(enabled), config_id),
        )
    else:
        config_id = uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO alert_configs
                (config_id, org_id, min_severity, telegram_chat_id, discord_webhook_url, enabled)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (config_id, org_id, min_severity, telegram_chat_id, discord_webhook_url, int(enabled)),
        )
    conn.commit()
    return config_id


def get_alert_config(conn: sqlite3.Connection, org_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM alert_configs WHERE org_id=?", (org_id,)
    ).fetchone()


def create_alert(
    conn: sqlite3.Connection,
    alert_type: str,
    severity: str,
    title: str,
    *,
    org_id: str | None = None,
    body: str | None = None,
    entity_id: str | None = None,
    action_id: str | None = None,
    run_id: str | None = None,
    data_json: str | None = None,
    dedup_key: str | None = None,
) -> str | None:
    """Create an alert. Returns alert_id, or None if dedup_key blocks it."""
    if dedup_key:
        existing = conn.execute(
            "SELECT alert_id FROM alerts WHERE dedup_key=? AND status='new'",
            (dedup_key,),
        ).fetchone()
        if existing:
            return None

    alert_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO alerts
            (alert_id, org_id, alert_type, severity, title, body,
             entity_id, action_id, run_id, data_json, dedup_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (alert_id, org_id, alert_type, severity, title, body,
         entity_id, action_id, run_id, data_json, dedup_key),
    )
    conn.commit()
    return alert_id


def acknowledge_alert(conn: sqlite3.Connection, alert_id: str, operator: str) -> None:
    conn.execute(
        """
        UPDATE alerts
        SET status='acknowledged', acknowledged_at=datetime('now'), acknowledged_by=?
        WHERE alert_id=?
        """,
        (operator, alert_id),
    )
    conn.commit()


def resolve_alert(conn: sqlite3.Connection, alert_id: str) -> None:
    conn.execute(
        "UPDATE alerts SET status='resolved', resolved_at=datetime('now') WHERE alert_id=?",
        (alert_id,),
    )
    conn.commit()


def list_alerts(
    conn: sqlite3.Connection,
    *,
    org_id: str | None = None,
    severity: str | None = None,
    status: str = "new",
    limit: int = 50,
) -> list[sqlite3.Row]:
    conditions = []
    params: list = []
    if org_id:
        conditions.append("org_id=?")
        params.append(org_id)
    if severity:
        conditions.append("severity=?")
        params.append(severity)
    if status:
        conditions.append("status=?")
        params.append(status)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)
    return conn.execute(
        f"SELECT * FROM alerts {where} ORDER BY created_at DESC LIMIT ?",
        params,
    ).fetchall()
