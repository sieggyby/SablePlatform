"""Alert evaluator: checks all registered conditions and fires alerts."""
from __future__ import annotations

import logging
import sqlite3

from sable_platform.db.alerts import create_alert

log = logging.getLogger(__name__)


def evaluate_alerts(
    conn: sqlite3.Connection,
    org_id: str | None = None,
) -> list[str]:
    """Run all alert checks for one org or all orgs. Returns list of created alert_ids."""
    if org_id and org_id != "_all":
        org_ids = [org_id]
    else:
        rows = conn.execute(
            "SELECT org_id FROM orgs WHERE status='active'"
        ).fetchall()
        org_ids = [r["org_id"] for r in rows]

    created: list[str] = []
    for oid in org_ids:
        created.extend(_check_tracking_stale(conn, oid))
        created.extend(_check_cultist_tag_expiring(conn, oid))
        created.extend(_check_sentiment_shift(conn, oid))
        created.extend(_check_mvl_score_change(conn, oid))
        created.extend(_check_actions_unclaimed(conn, oid))

    created.extend(_check_workflow_failures(conn, org_id if (org_id and org_id != "_all") else None))
    return created


def _check_tracking_stale(conn: sqlite3.Connection, org_id: str) -> list[str]:
    """Critical: tracking sync not completed in the last 14 days."""
    row = conn.execute(
        """
        SELECT completed_at FROM sync_runs
        WHERE org_id=? AND sync_type='sable_tracking' AND status='completed'
        ORDER BY completed_at DESC LIMIT 1
        """,
        (org_id,),
    ).fetchone()

    is_stale = False
    age_days = None
    if not row or not row["completed_at"]:
        is_stale = True
    else:
        stale_check = conn.execute(
            "SELECT julianday('now') - julianday(?) AS age_days",
            (row["completed_at"],),
        ).fetchone()
        if stale_check and stale_check["age_days"] is not None:
            age_days = int(stale_check["age_days"])
            is_stale = age_days > 14

    if not is_stale:
        return []

    age_str = f"{age_days} days" if age_days else "never"
    alert_id = create_alert(
        conn,
        alert_type="tracking_stale",
        severity="critical",
        title=f"Tracking data is stale ({age_str})",
        org_id=org_id,
        body=f"Org {org_id} has not had a successful tracking sync in {age_str}.",
        dedup_key=f"tracking_stale:{org_id}",
    )
    if alert_id:
        _deliver(conn, org_id, "critical", f"[CRITICAL] Tracking stale for {org_id}: {age_str}")
        return [alert_id]
    return []


def _check_cultist_tag_expiring(conn: sqlite3.Connection, org_id: str) -> list[str]:
    """Warning: cultist_candidate tag expires within 7 days."""
    try:
        rows = conn.execute(
            """
            SELECT t.entity_id, t.expires_at
            FROM entity_tags t
            JOIN entities e ON t.entity_id = e.entity_id
            WHERE e.org_id=?
              AND t.tag='cultist_candidate'
              AND t.is_current=1
              AND t.expires_at IS NOT NULL
              AND julianday(t.expires_at) - julianday('now') BETWEEN 0 AND 7
            """,
            (org_id,),
        ).fetchall()
    except Exception as e:
        log.warning("Alert check cultist_tag_expiring failed: %s", e)
        return []

    created = []
    for r in rows:
        entity_id = r["entity_id"]
        alert_id = create_alert(
            conn,
            alert_type="cultist_tag_expiring",
            severity="warning",
            title=f"cultist_candidate tag expires soon for {entity_id[:8]}",
            org_id=org_id,
            entity_id=entity_id,
            body=f"Tag expires {r['expires_at']} — refresh diagnostic or the tag will lapse.",
            dedup_key=f"tag_expiring:{entity_id}:cultist_candidate",
        )
        if alert_id:
            _deliver(conn, org_id, "warning", f"[WARNING] cultist_candidate expiring for entity {entity_id[:8]}")
            created.append(alert_id)
    return created


def _check_sentiment_shift(conn: sqlite3.Connection, org_id: str) -> list[str]:
    """Warning: sentiment_negative jumped from <10% to >20%."""
    try:
        rows = conn.execute(
            """
            SELECT run_id_after, value_before, value_after
            FROM diagnostic_deltas
            WHERE org_id=? AND metric_name='sentiment_negative'
              AND value_after > 0.20 AND value_before < 0.10
            ORDER BY created_at DESC LIMIT 5
            """,
            (org_id,),
        ).fetchall()
    except Exception as e:
        log.warning("Alert check sentiment_shift failed: %s", e)
        return []

    created = []
    for r in rows:
        alert_id = create_alert(
            conn,
            alert_type="sentiment_shift",
            severity="warning",
            title=f"Negative sentiment spike ({r['value_before']:.0%} → {r['value_after']:.0%})",
            org_id=org_id,
            body=f"sentiment_negative rose from {r['value_before']:.1%} to {r['value_after']:.1%}.",
            dedup_key=f"sentiment_shift:{org_id}:{r['run_id_after']}",
        )
        if alert_id:
            _deliver(conn, org_id, "warning", f"[WARNING] Sentiment spike for {org_id}")
            created.append(alert_id)
    return created


def _check_mvl_score_change(conn: sqlite3.Connection, org_id: str) -> list[str]:
    """Info: MVL stack score changed by 1 or more."""
    try:
        rows = conn.execute(
            """
            SELECT run_id_after, value_before, value_after, delta
            FROM diagnostic_deltas
            WHERE org_id=? AND metric_name='mvl_stack_score'
              AND ABS(delta) >= 1
            ORDER BY created_at DESC LIMIT 5
            """,
            (org_id,),
        ).fetchall()
    except Exception as e:
        log.warning("Alert check mvl_score_change failed: %s", e)
        return []

    created = []
    for r in rows:
        direction = "increased" if (r["delta"] or 0) > 0 else "decreased"
        alert_id = create_alert(
            conn,
            alert_type="mvl_score_change",
            severity="info",
            title=f"MVL stack score {direction} ({r['value_before']:.0f} → {r['value_after']:.0f})",
            org_id=org_id,
            body=f"mvl_stack_score changed from {r['value_before']} to {r['value_after']}.",
            dedup_key=f"mvl_change:{org_id}:{r['run_id_after']}",
        )
        if alert_id:
            _deliver(conn, org_id, "info", f"[INFO] MVL score change for {org_id}")
            created.append(alert_id)
    return created


def _check_actions_unclaimed(conn: sqlite3.Connection, org_id: str) -> list[str]:
    """Info: actions pending for more than 7 days without being claimed."""
    try:
        rows = conn.execute(
            """
            SELECT action_id, title
            FROM actions
            WHERE org_id=? AND status='pending'
              AND julianday('now') - julianday(created_at) > 7
            """,
            (org_id,),
        ).fetchall()
    except Exception as e:
        log.warning("Alert check action_unclaimed failed: %s", e)
        return []

    created = []
    for r in rows:
        alert_id = create_alert(
            conn,
            alert_type="action_unclaimed",
            severity="info",
            title=f"Action unclaimed: \"{(r['title'] or '')[:60]}\"",
            org_id=org_id,
            action_id=r["action_id"],
            body=f"Action {r['action_id'][:8]} has been pending for >7 days with no operator.",
            dedup_key=f"unclaimed:{r['action_id']}",
        )
        if alert_id:
            _deliver(conn, org_id, "info", f"[INFO] Unclaimed action for {org_id}: {r['action_id'][:8]}")
            created.append(alert_id)
    return created


def _check_workflow_failures(
    conn: sqlite3.Connection,
    org_id: str | None,
) -> list[str]:
    """Critical: workflow_runs with status='failed' that have no open alert."""
    conditions = "WHERE status='failed'"
    params: list = []
    if org_id:
        conditions += " AND org_id=?"
        params.append(org_id)

    rows = conn.execute(
        f"SELECT run_id, org_id, workflow_name FROM workflow_runs {conditions}",
        params,
    ).fetchall()

    created = []
    for r in rows:
        alert_id = create_alert(
            conn,
            alert_type="workflow_failed",
            severity="critical",
            title=f"Workflow failed: {r['workflow_name']}",
            org_id=r["org_id"],
            run_id=r["run_id"],
            body=f"Run {r['run_id'][:12]} failed. Use 'sable-platform workflow resume {r['run_id']}' to retry.",
            dedup_key=f"workflow_failed:{r['run_id']}",
        )
        if alert_id:
            _deliver(conn, r["org_id"], "critical",
                     f"[CRITICAL] Workflow {r['workflow_name']} failed ({r['run_id'][:12]})")
            created.append(alert_id)
    return created


def _deliver(conn: sqlite3.Connection, org_id: str | None, severity: str, message: str) -> None:
    """Deliver alert to configured channels. v1: log only."""
    if not org_id:
        log.warning("ALERT %s: %s", severity.upper(), message)
        return

    try:
        config = conn.execute(
            "SELECT min_severity, enabled FROM alert_configs WHERE org_id=?", (org_id,)
        ).fetchone()
    except Exception:
        config = None

    if config and not config["enabled"]:
        return

    severity_ranks = {"critical": 3, "warning": 2, "info": 1}
    min_sev = config["min_severity"] if config else "warning"
    if severity_ranks.get(severity, 0) < severity_ranks.get(min_sev, 2):
        return

    log.warning("ALERT %s [%s]: %s", severity.upper(), org_id, message)
    # v2: send to telegram_chat_id / discord_webhook_url if configured
