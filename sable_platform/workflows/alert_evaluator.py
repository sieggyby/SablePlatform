"""Alert evaluator: orchestrates all registered condition checks."""
from __future__ import annotations

import logging
import sqlite3

from sable_platform.workflows.alert_checks import (
    _check_actions_unclaimed,
    _check_bridge_decay,
    _check_cultist_tag_expiring,
    _check_discord_pulse_regression,
    _check_discord_pulse_stale,
    _check_member_decay,
    _check_mvl_score_change,
    _check_sentiment_shift,
    _check_stuck_runs,
    _check_tracking_stale,
    _check_watchlist_changes,
    _check_workflow_failures,
)

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
        try:
            created.extend(_check_tracking_stale(conn, oid))
            created.extend(_check_cultist_tag_expiring(conn, oid))
            created.extend(_check_sentiment_shift(conn, oid))
            created.extend(_check_mvl_score_change(conn, oid))
            created.extend(_check_actions_unclaimed(conn, oid))
            created.extend(_check_discord_pulse_stale(conn, oid))
            created.extend(_check_stuck_runs(conn, oid))
            created.extend(_check_member_decay(conn, oid))
            created.extend(_check_bridge_decay(conn, oid))
            created.extend(_check_watchlist_changes(conn, oid))
        except Exception as exc:
            log.error("evaluate_alerts: unexpected error for org %s, skipping: %s", oid, exc)

    try:
        created.extend(_check_workflow_failures(conn, org_id if (org_id and org_id != "_all") else None))
    except Exception as exc:
        log.error("evaluate_alerts: unexpected error in workflow_failures check, skipping: %s", exc)
    for oid in org_ids:
        try:
            created.extend(_check_discord_pulse_regression(conn, oid))
        except Exception as exc:
            log.error("evaluate_alerts: unexpected error for org %s in regression check: %s", oid, exc)

    try:
        conn.execute(
            "INSERT INTO platform_meta (key, value, updated_at) VALUES ('last_alert_eval_at', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at"
        )
        conn.commit()
    except Exception as exc:
        log.warning("evaluate_alerts: failed to write heartbeat to platform_meta: %s", exc)

    return created
