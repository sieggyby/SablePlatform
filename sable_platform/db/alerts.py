"""Alert and alert config helpers for sable.db."""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection


def upsert_alert_config(
    conn: Connection,
    org_id: str,
    *,
    min_severity: str = "warning",
    telegram_chat_id: str | None = None,
    discord_webhook_url: str | None = None,
    enabled: bool = True,
    cooldown_hours: int | None = None,
) -> str:
    """Create or update the alert config for an org. Returns config_id."""
    existing = conn.execute(
        text("SELECT config_id FROM alert_configs WHERE org_id=:org_id"), {"org_id": org_id}
    ).fetchone()
    if existing:
        config_id = existing["config_id"]
        conn.execute(
            text("""
            UPDATE alert_configs
            SET min_severity=:min_severity, telegram_chat_id=:telegram_chat_id,
                discord_webhook_url=:discord_webhook_url, enabled=:enabled,
                cooldown_hours = COALESCE(:cooldown_hours, cooldown_hours)
            WHERE config_id=:config_id
            """),
            {"min_severity": min_severity, "telegram_chat_id": telegram_chat_id,
             "discord_webhook_url": discord_webhook_url, "enabled": int(enabled),
             "cooldown_hours": cooldown_hours, "config_id": config_id},
        )
    else:
        config_id = uuid.uuid4().hex
        if cooldown_hours is not None:
            conn.execute(
                text("""
                INSERT INTO alert_configs
                    (config_id, org_id, min_severity, telegram_chat_id, discord_webhook_url, enabled, cooldown_hours)
                VALUES (:config_id, :org_id, :min_severity, :telegram_chat_id, :discord_webhook_url, :enabled, :cooldown_hours)
                """),
                {"config_id": config_id, "org_id": org_id, "min_severity": min_severity,
                 "telegram_chat_id": telegram_chat_id, "discord_webhook_url": discord_webhook_url,
                 "enabled": int(enabled), "cooldown_hours": cooldown_hours},
            )
        else:
            conn.execute(
                text("""
                INSERT INTO alert_configs
                    (config_id, org_id, min_severity, telegram_chat_id, discord_webhook_url, enabled)
                VALUES (:config_id, :org_id, :min_severity, :telegram_chat_id, :discord_webhook_url, :enabled)
                """),
                {"config_id": config_id, "org_id": org_id, "min_severity": min_severity,
                 "telegram_chat_id": telegram_chat_id, "discord_webhook_url": discord_webhook_url,
                 "enabled": int(enabled)},
            )
    conn.commit()
    return config_id


def get_alert_config(conn: Connection, org_id: str):
    return conn.execute(
        text("SELECT * FROM alert_configs WHERE org_id=:org_id"), {"org_id": org_id}
    ).fetchone()


def create_alert(
    conn: Connection,
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
            text("SELECT alert_id FROM alerts WHERE dedup_key=:dedup_key AND status IN ('new', 'acknowledged')"),
            {"dedup_key": dedup_key},
        ).fetchone()
        if existing:
            return None

    alert_id = uuid.uuid4().hex
    conn.execute(
        text("""
        INSERT INTO alerts
            (alert_id, org_id, alert_type, severity, title, body,
             entity_id, action_id, run_id, data_json, dedup_key)
        VALUES (:alert_id, :org_id, :alert_type, :severity, :title, :body,
                :entity_id, :action_id, :run_id, :data_json, :dedup_key)
        """),
        {"alert_id": alert_id, "org_id": org_id, "alert_type": alert_type,
         "severity": severity, "title": title, "body": body,
         "entity_id": entity_id, "action_id": action_id, "run_id": run_id,
         "data_json": data_json, "dedup_key": dedup_key},
    )
    conn.commit()
    return alert_id


def acknowledge_alert(conn: Connection, alert_id: str, operator: str) -> None:
    row = conn.execute(text("SELECT org_id FROM alerts WHERE alert_id=:alert_id"), {"alert_id": alert_id}).fetchone()
    conn.execute(
        text("""
        UPDATE alerts
        SET status='acknowledged', acknowledged_at=datetime('now'), acknowledged_by=:operator
        WHERE alert_id=:alert_id
        """),
        {"operator": operator, "alert_id": alert_id},
    )
    conn.commit()
    from sable_platform.db.audit import log_audit
    log_audit(conn, operator, "alert_acknowledge",
              org_id=row["org_id"] if row else None,
              detail={"alert_id": alert_id})


def resolve_alert(conn: Connection, alert_id: str) -> None:
    row = conn.execute(text("SELECT org_id FROM alerts WHERE alert_id=:alert_id"), {"alert_id": alert_id}).fetchone()
    conn.execute(
        text("UPDATE alerts SET status='resolved', resolved_at=datetime('now') WHERE alert_id=:alert_id"),
        {"alert_id": alert_id},
    )
    conn.commit()
    from sable_platform.db.audit import log_audit
    log_audit(conn, "system", "alert_resolve",
              org_id=row["org_id"] if row else None,
              detail={"alert_id": alert_id}, source="system")


def get_last_delivered_at(conn: Connection, dedup_key: str) -> str | None:
    """Get most recent last_delivered_at for any alert with this dedup_key (any status)."""
    row = conn.execute(
        text("""
        SELECT last_delivered_at FROM alerts
        WHERE dedup_key=:dedup_key AND last_delivered_at IS NOT NULL
        ORDER BY last_delivered_at DESC LIMIT 1
        """),
        {"dedup_key": dedup_key},
    ).fetchone()
    return row["last_delivered_at"] if row else None


def mark_delivered(conn: Connection, dedup_key: str) -> None:
    """Set last_delivered_at=now on the current 'new' alert for this dedup_key."""
    conn.execute(
        text("UPDATE alerts SET last_delivered_at=datetime('now'), last_delivery_error=NULL "
             "WHERE dedup_key=:dedup_key AND status='new'"),
        {"dedup_key": dedup_key},
    )
    conn.commit()


def mark_delivery_failed(conn: Connection, dedup_key: str, error: str) -> None:
    """Record a delivery failure on the current 'new' alert for this dedup_key."""
    conn.execute(
        text("UPDATE alerts SET last_delivery_error=:error WHERE dedup_key=:dedup_key AND status='new'"),
        {"error": error[:500], "dedup_key": dedup_key},
    )
    conn.commit()


def list_alerts(
    conn: Connection,
    *,
    org_id: str | None = None,
    severity: str | None = None,
    status: str = "new",
    limit: int = 50,
) -> list:
    conditions = []
    params: dict = {}
    if org_id:
        conditions.append("org_id=:org_id")
        params["org_id"] = org_id
    if severity:
        conditions.append("severity=:severity")
        params["severity"] = severity
    if status:
        conditions.append("status=:status")
        params["status"] = status
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params["limit"] = limit
    return conn.execute(
        text(f"SELECT * FROM alerts {where} ORDER BY created_at DESC LIMIT :limit"),
        params,
    ).fetchall()
