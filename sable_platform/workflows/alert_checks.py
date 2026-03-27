"""Alert condition checks for sable.db."""
from __future__ import annotations

import logging
import sqlite3

from sable_platform.db.alerts import create_alert
from sable_platform.workflows.alert_delivery import _deliver

log = logging.getLogger(__name__)

TRACKING_STALE_DAYS = 14
DISCORD_PULSE_REGRESSION_THRESHOLD = 0.05
DISCORD_PULSE_STALE_DAYS = 7
STUCK_RUN_THRESHOLD_HOURS = 2


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
            is_stale = age_days > TRACKING_STALE_DAYS

    if not is_stale:
        return []

    age_str = f"{age_days} days" if age_days else "never"
    dedup_key = f"tracking_stale:{org_id}"
    alert_id = create_alert(
        conn,
        alert_type="tracking_stale",
        severity="critical",
        title=f"Tracking data is stale ({age_str})",
        org_id=org_id,
        body=f"Org {org_id} has not had a successful tracking sync in {age_str}.",
        dedup_key=dedup_key,
    )
    if alert_id:
        _deliver(conn, org_id, "critical", f"[CRITICAL] Tracking stale for {org_id}: {age_str}",
                 dedup_key=dedup_key)
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
        dedup_key = f"tag_expiring:{entity_id}:cultist_candidate"
        alert_id = create_alert(
            conn,
            alert_type="cultist_tag_expiring",
            severity="warning",
            title=f"cultist_candidate tag expires soon for {entity_id[:8]}",
            org_id=org_id,
            entity_id=entity_id,
            body=f"Tag expires {r['expires_at']} — refresh diagnostic or the tag will lapse.",
            dedup_key=dedup_key,
        )
        if alert_id:
            _deliver(conn, org_id, "warning", f"[WARNING] cultist_candidate expiring for entity {entity_id[:8]}",
                     dedup_key=dedup_key)
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
        dedup_key = f"sentiment_shift:{org_id}:{r['run_id_after']}"
        alert_id = create_alert(
            conn,
            alert_type="sentiment_shift",
            severity="warning",
            title=f"Negative sentiment spike ({r['value_before']:.0%} → {r['value_after']:.0%})",
            org_id=org_id,
            body=f"sentiment_negative rose from {r['value_before']:.1%} to {r['value_after']:.1%}.",
            dedup_key=dedup_key,
        )
        if alert_id:
            _deliver(conn, org_id, "warning", f"[WARNING] Sentiment spike for {org_id}",
                     dedup_key=dedup_key)
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
        dedup_key = f"mvl_change:{org_id}:{r['run_id_after']}"
        alert_id = create_alert(
            conn,
            alert_type="mvl_score_change",
            severity="info",
            title=f"MVL stack score {direction} ({r['value_before']:.0f} → {r['value_after']:.0f})",
            org_id=org_id,
            body=f"mvl_stack_score changed from {r['value_before']} to {r['value_after']}.",
            dedup_key=dedup_key,
        )
        if alert_id:
            _deliver(conn, org_id, "info", f"[INFO] MVL score change for {org_id}",
                     dedup_key=dedup_key)
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
        dedup_key = f"unclaimed:{r['action_id']}"
        alert_id = create_alert(
            conn,
            alert_type="action_unclaimed",
            severity="info",
            title=f"Action unclaimed: \"{(r['title'] or '')[:60]}\"",
            org_id=org_id,
            action_id=r["action_id"],
            body=f"Action {r['action_id'][:8]} has been pending for >7 days with no operator.",
            dedup_key=dedup_key,
        )
        if alert_id:
            _deliver(conn, org_id, "info", f"[INFO] Unclaimed action for {org_id}: {r['action_id'][:8]}",
                     dedup_key=dedup_key)
            created.append(alert_id)
    return created


def _check_workflow_failures(
    conn: sqlite3.Connection,
    org_id: str | None,
) -> list[str]:
    """Critical: workflow_runs with status='failed' that have no open alert."""
    conditions = "WHERE status='failed' AND (created_at IS NULL OR created_at > datetime('now', '-30 days'))"
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
        dedup_key = f"workflow_failed:{r['run_id']}"
        alert_id = create_alert(
            conn,
            alert_type="workflow_failed",
            severity="critical",
            title=f"Workflow failed: {r['workflow_name']}",
            org_id=r["org_id"],
            run_id=r["run_id"],
            body=f"Run {r['run_id'][:12]} failed. Use 'sable-platform workflow resume {r['run_id']}' to retry.",
            dedup_key=dedup_key,
        )
        if alert_id:
            _deliver(conn, r["org_id"], "critical",
                     f"[CRITICAL] Workflow {r['workflow_name']} failed ({r['run_id'][:12]})",
                     dedup_key=dedup_key)
            created.append(alert_id)
    return created


def _check_discord_pulse_regression(conn: sqlite3.Connection, org_id: str) -> list[str]:
    """Warning: wow_retention_rate dropped >5% WoW (retention_delta < -threshold)."""
    try:
        rows = conn.execute(
            """
            SELECT run_date, project_slug, wow_retention_rate, retention_delta
            FROM discord_pulse_runs
            WHERE org_id=? AND retention_delta IS NOT NULL AND retention_delta < ?
            ORDER BY run_date DESC LIMIT 5
            """,
            (org_id, -DISCORD_PULSE_REGRESSION_THRESHOLD),
        ).fetchall()
    except Exception as e:
        log.warning("Alert check discord_pulse_regression failed: %s", e)
        return []

    created = []
    for r in rows:
        pct_before = (r["wow_retention_rate"] - r["retention_delta"]) if r["wow_retention_rate"] is not None else None
        title_parts = []
        if pct_before is not None:
            title_parts.append(f"{pct_before:.0%} → {r['wow_retention_rate']:.0%}")
        title = f"Discord retention dropped ({', '.join(title_parts)})" if title_parts else "Discord retention dropped"
        dedup_key = f"discord_pulse_regression:{org_id}:{r['project_slug']}:{r['run_date']}"
        alert_id = create_alert(
            conn,
            alert_type="discord_pulse_regression",
            severity="warning",
            title=title,
            org_id=org_id,
            body=(
                f"wow_retention_rate fell by {abs(r['retention_delta']):.1%} on {r['run_date']} "
                f"(project: {r['project_slug']})."
            ),
            dedup_key=dedup_key,
        )
        if alert_id:
            _deliver(conn, org_id, "warning",
                     f"[WARNING] Discord retention drop for {org_id}/{r['project_slug']} on {r['run_date']}",
                     dedup_key=dedup_key)
            created.append(alert_id)
    return created


def _check_discord_pulse_stale(conn: sqlite3.Connection, org_id: str) -> list[str]:
    """Warning: no discord_pulse_run data in the last 7 days."""
    try:
        row = conn.execute(
            """
            SELECT run_date FROM discord_pulse_runs
            WHERE org_id=?
            ORDER BY run_date DESC LIMIT 1
            """,
            (org_id,),
        ).fetchone()
    except Exception as e:
        log.warning("Alert check discord_pulse_stale failed: %s", e)
        return []

    is_stale = False
    age_days = None
    if not row:
        is_stale = True
    else:
        stale_check = conn.execute(
            "SELECT julianday('now') - julianday(?) AS age_days",
            (row["run_date"],),
        ).fetchone()
        if stale_check and stale_check["age_days"] is not None:
            age_days = int(stale_check["age_days"])
            is_stale = age_days > DISCORD_PULSE_STALE_DAYS

    if not is_stale:
        return []

    age_str = f"{age_days} days" if age_days is not None else "never"
    dedup_key = f"discord_pulse_stale:{org_id}"
    alert_id = create_alert(
        conn,
        alert_type="discord_pulse_stale",
        severity="warning",
        title=f"Discord pulse data is stale ({age_str})",
        org_id=org_id,
        body=f"Org {org_id} has no Discord pulse data in the last {age_str}.",
        dedup_key=dedup_key,
    )
    if alert_id:
        _deliver(conn, org_id, "warning", f"[WARNING] Discord pulse stale for {org_id}: {age_str}",
                 dedup_key=dedup_key)
        return [alert_id]
    return []


def _check_stuck_runs(conn: sqlite3.Connection, org_id: str) -> list[str]:
    """Warning: workflow_runs stuck in 'running' state for more than STUCK_RUN_THRESHOLD_HOURS."""
    try:
        rows = conn.execute(
            """
            SELECT run_id, workflow_name FROM workflow_runs
            WHERE org_id=? AND status='running'
              AND started_at < datetime('now', ?)
            """,
            (org_id, f'-{STUCK_RUN_THRESHOLD_HOURS} hours'),
        ).fetchall()
    except Exception as e:
        log.warning("Alert check stuck_runs failed: %s", e)
        return []

    created = []
    for r in rows:
        dedup_key = f"stuck_run:{r['run_id']}"
        alert_id = create_alert(
            conn,
            alert_type="stuck_run",
            severity="warning",
            title=f"Workflow run stuck: {r['workflow_name']}",
            org_id=org_id,
            run_id=r["run_id"],
            body=(
                f"Run {r['run_id'][:12]} has been 'running' for >{STUCK_RUN_THRESHOLD_HOURS} hours. "
                f"Use 'sable-platform workflow gc' to mark as timed_out."
            ),
            dedup_key=dedup_key,
        )
        if alert_id:
            _deliver(conn, org_id, "warning",
                     f"[WARNING] Stuck workflow run {r['workflow_name']} ({r['run_id'][:12]})",
                     dedup_key=dedup_key)
            created.append(alert_id)
    return created
