"""Alert condition checks for sable.db."""
from __future__ import annotations

import json
import logging
import sqlite3

from sable_platform.db.alerts import create_alert
from sable_platform.db.centrality import BRIDGE_CENTRALITY_THRESHOLD, BRIDGE_DECAY_THRESHOLD
from sable_platform.db.decay import DECAY_WARNING_THRESHOLD, DECAY_CRITICAL_THRESHOLD
from sable_platform.db.watchlist import take_all_snapshots, get_watchlist_changes
from sable_platform.workflows.alert_delivery import _deliver

log = logging.getLogger(__name__)

TRACKING_STALE_DAYS = 14
DISCORD_PULSE_REGRESSION_THRESHOLD = 0.05
DISCORD_PULSE_STALE_DAYS = 7
STUCK_RUN_THRESHOLD_HOURS = 2


def _check_tracking_stale(conn: sqlite3.Connection, org_id: str) -> list[str]:
    """Critical: tracking sync not completed in the last 14 days."""
    stale_days = TRACKING_STALE_DAYS
    try:
        org_row = conn.execute("SELECT config_json FROM orgs WHERE org_id=?", (org_id,)).fetchone()
        if org_row and org_row["config_json"]:
            cfg = json.loads(org_row["config_json"])
            stale_days = cfg.get("tracking_stale_days", stale_days)
    except Exception as e:
        log.warning("Failed to parse tracking_stale config for org %s, using defaults: %s", org_id, e)

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
            is_stale = age_days > stale_days

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
        dedup_key = f"unclaimed:{org_id}:{r['action_id']}"
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
        dedup_key = f"workflow_failed:{r['org_id']}:{r['run_id']}"
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
    stale_days = DISCORD_PULSE_STALE_DAYS
    try:
        org_row = conn.execute("SELECT config_json FROM orgs WHERE org_id=?", (org_id,)).fetchone()
        if org_row and org_row["config_json"]:
            cfg = json.loads(org_row["config_json"])
            stale_days = cfg.get("discord_pulse_stale_days", stale_days)
    except Exception as e:
        log.warning("Failed to parse discord_pulse_stale config for org %s, using defaults: %s", org_id, e)

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
            is_stale = age_days > stale_days

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
    threshold_hours = STUCK_RUN_THRESHOLD_HOURS
    try:
        org_row = conn.execute("SELECT config_json FROM orgs WHERE org_id=?", (org_id,)).fetchone()
        if org_row and org_row["config_json"]:
            cfg = json.loads(org_row["config_json"])
            threshold_hours = cfg.get("stuck_run_threshold_hours", threshold_hours)
    except Exception as e:
        log.warning("Failed to parse stuck_run config for org %s, using defaults: %s", org_id, e)

    try:
        rows = conn.execute(
            """
            SELECT run_id, workflow_name FROM workflow_runs
            WHERE org_id=? AND status='running'
              AND started_at < datetime('now', ?)
            """,
            (org_id, f'-{threshold_hours} hours'),
        ).fetchall()
    except Exception as e:
        log.warning("Alert check stuck_runs failed: %s", e)
        return []

    created = []
    for r in rows:
        dedup_key = f"stuck_run:{org_id}:{r['run_id']}"
        alert_id = create_alert(
            conn,
            alert_type="stuck_run",
            severity="warning",
            title=f"Workflow run stuck: {r['workflow_name']}",
            org_id=org_id,
            run_id=r["run_id"],
            body=(
                f"Run {r['run_id'][:12]} has been 'running' for >{threshold_hours} hours. "
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


_STRUCTURALLY_IMPORTANT_TAGS = frozenset({
    "cultist_candidate", "cultist", "voice", "mvl", "top_contributor",
})


def _check_member_decay(conn: sqlite3.Connection, org_id: str) -> list[str]:
    """Warning/Critical: entity decay score exceeds threshold."""
    warning_threshold = DECAY_WARNING_THRESHOLD
    critical_threshold = DECAY_CRITICAL_THRESHOLD
    try:
        org_row = conn.execute(
            "SELECT config_json FROM orgs WHERE org_id=?", (org_id,),
        ).fetchone()
        if org_row and org_row["config_json"]:
            cfg = json.loads(org_row["config_json"])
            warning_threshold = cfg.get("decay_warning_threshold", warning_threshold)
            critical_threshold = cfg.get("decay_critical_threshold", critical_threshold)
    except Exception as e:
        log.warning("Failed to parse decay config for org %s, using defaults: %s", org_id, e)

    try:
        rows = conn.execute(
            """
            SELECT entity_id, decay_score, risk_tier, factors_json
            FROM entity_decay_scores
            WHERE org_id=? AND decay_score >= ?
            """,
            (org_id, warning_threshold),
        ).fetchall()
    except Exception as e:
        log.warning("Alert check member_decay failed: %s", e)
        return []

    created = []
    for r in rows:
        entity_id = r["entity_id"]
        score = r["decay_score"]

        # Determine if entity_id is a real entity (vs raw handle fallback)
        is_real_entity = conn.execute(
            "SELECT 1 FROM entities WHERE entity_id=?", (entity_id,),
        ).fetchone() is not None

        # Check if entity has a structurally important tag (for critical escalation)
        has_important_tag = False
        if is_real_entity:
            try:
                tag_row = conn.execute(
                    """
                    SELECT 1 FROM entity_tags
                    WHERE entity_id=? AND is_current=1 AND tag IN ({})
                    LIMIT 1
                    """.format(",".join("?" for _ in _STRUCTURALLY_IMPORTANT_TAGS)),
                    (entity_id, *_STRUCTURALLY_IMPORTANT_TAGS),
                ).fetchone()
                has_important_tag = tag_row is not None
            except Exception as e:
                log.warning("Failed to check important tags for entity %s: %s", entity_id, e)

        if score >= critical_threshold and has_important_tag:
            severity = "critical"
        else:
            severity = "warning"

        dedup_key = f"member_decay:{org_id}:{entity_id}"
        alert_id = create_alert(
            conn,
            alert_type="member_decay",
            severity=severity,
            title=f"Member at risk: {entity_id[:16]} (score {score:.2f})",
            org_id=org_id,
            entity_id=entity_id if is_real_entity else None,
            body=f"Decay score {score:.2f} ({r['risk_tier']}) for {entity_id}.",
            dedup_key=dedup_key,
        )
        if alert_id:
            label = "CRITICAL" if severity == "critical" else "WARNING"
            _deliver(conn, org_id, severity,
                     f"[{label}] Member decay for {org_id}: {entity_id[:16]} score={score:.2f}",
                     dedup_key=dedup_key)
            created.append(alert_id)
    return created


def _check_bridge_decay(conn: sqlite3.Connection, org_id: str) -> list[str]:
    """Critical: high-centrality bridge node with high decay score."""
    centrality_threshold = BRIDGE_CENTRALITY_THRESHOLD
    decay_threshold = BRIDGE_DECAY_THRESHOLD
    try:
        org_row = conn.execute(
            "SELECT config_json FROM orgs WHERE org_id=?", (org_id,),
        ).fetchone()
        if org_row and org_row["config_json"]:
            cfg = json.loads(org_row["config_json"])
            centrality_threshold = cfg.get("bridge_centrality_threshold", centrality_threshold)
            decay_threshold = cfg.get("bridge_decay_threshold", decay_threshold)
    except Exception as e:
        log.warning("Failed to parse bridge decay config for org %s, using defaults: %s", org_id, e)

    try:
        rows = conn.execute(
            """
            SELECT c.entity_id, c.degree_centrality, d.decay_score
            FROM entity_centrality_scores c
            JOIN entity_decay_scores d ON c.org_id = d.org_id AND c.entity_id = d.entity_id
            WHERE c.org_id = ? AND c.degree_centrality >= ? AND d.decay_score >= ?
            """,
            (org_id, centrality_threshold, decay_threshold),
        ).fetchall()
    except Exception as e:
        log.warning("Alert check bridge_decay failed: %s", e)
        return []

    created = []
    for r in rows:
        entity_id = r["entity_id"]
        dedup_key = f"bridge_decay:{org_id}:{entity_id}"
        alert_id = create_alert(
            conn,
            alert_type="bridge_decay",
            severity="critical",
            title=f"Bridge node at risk: {entity_id[:16]} (centrality {r['degree_centrality']:.2f}, decay {r['decay_score']:.2f})",
            org_id=org_id,
            body=f"High-centrality bridge node {entity_id} has decay score {r['decay_score']:.2f}.",
            dedup_key=dedup_key,
        )
        if alert_id:
            _deliver(conn, org_id, "critical",
                     f"[CRITICAL] Bridge node decay for {org_id}: {entity_id[:16]}",
                     dedup_key=dedup_key)
            created.append(alert_id)
    return created


def _check_watchlist_changes(conn: sqlite3.Connection, org_id: str) -> list[str]:
    """Warning/Critical: watched entity state changed."""
    try:
        take_all_snapshots(conn, org_id)
        changes = get_watchlist_changes(conn, org_id)
    except Exception as e:
        log.warning("Alert check watchlist_changes failed: %s", e)
        return []

    created = []
    for entry in changes:
        entity_id = entry["entity_id"]
        change_list = entry["changes"]

        # Skip "newly watched" entries
        if change_list == ["newly watched"]:
            continue

        # Determine severity — critical if decay increased by >= 0.1
        severity = "warning"
        for ch in change_list:
            if ch.startswith("decay_score:"):
                try:
                    parts = ch.split("->")
                    old_val = parts[0].split(":")[1].strip()
                    new_val = parts[1].strip()
                    if old_val != "None" and new_val != "None":
                        if float(new_val) - float(old_val) >= 0.1:
                            severity = "critical"
                except (ValueError, IndexError):
                    pass

        changes_summary = "; ".join(change_list[:3])
        dedup_key = f"watchlist_change:{org_id}:{entity_id}"
        alert_id = create_alert(
            conn,
            alert_type="watchlist_change",
            severity=severity,
            title=f"Watched member {entity_id[:16]} changed: {changes_summary}",
            org_id=org_id,
            body=f"Changes detected: {'; '.join(change_list)}",
            dedup_key=dedup_key,
        )
        if alert_id:
            label = "CRITICAL" if severity == "critical" else "WARNING"
            _deliver(conn, org_id, severity,
                     f"[{label}] Watchlist change for {org_id}: {entity_id[:16]}",
                     dedup_key=dedup_key)
            created.append(alert_id)
    return created
