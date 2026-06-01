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


def _run_check(conn, fn, *args) -> list[str]:
    """Run one alert check in isolation.

    Commits the check's alerts on success; rolls back on failure. On Postgres a
    failed statement aborts the whole transaction ("current transaction is
    aborted, commands ignored…"), so without this boundary one broken check
    (e.g. a dialect issue) would poison every sibling check AND the final
    heartbeat/commit — which is exactly why no alerts (incl. tracking_stale)
    ever fired in prod. Isolating per check keeps the others working.
    """
    try:
        ids = fn(conn, *args)
        conn.commit()
        return ids
    except Exception as exc:  # noqa: BLE001 — one bad check must not sink the rest
        try:
            conn.rollback()
        except Exception:
            pass
        log.error("evaluate_alerts: check %s failed, skipping: %s", getattr(fn, "__name__", fn), exc)
        return []


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
    # Per-org checks, each run in its own commit/rollback boundary (see _run_check)
    # so a single check failing on Postgres can't abort the whole evaluation.
    _per_org_checks = [
        _check_tracking_stale,
        _check_cultist_tag_expiring,
        _check_sentiment_shift,
        _check_mvl_score_change,
        _check_actions_unclaimed,
        _check_discord_pulse_stale,
        _check_stuck_runs,
        _check_member_decay,
        _check_bridge_decay,
        _check_watchlist_changes,
    ]
    for oid in org_ids:
        for check in _per_org_checks:
            created.extend(_run_check(conn, check, oid))

    created.extend(
        _run_check(conn, _check_workflow_failures, org_id if (org_id and org_id != "_all") else None)
    )
    for oid in org_ids:
        created.extend(_run_check(conn, _check_discord_pulse_regression, oid))

    try:
        conn.execute(
            "INSERT INTO platform_meta (key, value, updated_at) VALUES ('last_alert_eval_at', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at"
        )
        conn.commit()
    except Exception as exc:
        log.warning("evaluate_alerts: failed to write heartbeat to platform_meta: %s", exc)

    return created
