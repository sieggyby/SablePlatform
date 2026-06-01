"""Evaluate + deliver alerts for an org (or all active orgs).

Entry point for the VPS alerts timer (deploy/sable-platform-alerts.{service,timer}).
The alert *checks* (incl. ``tracking_stale``, which catches a stalled
SableTracking→content_items sync) already exist and are registered in
``evaluate_alerts``; this just runs them on a schedule and delivers any that
fire. Without a configured delivery channel (alert_configs.telegram_chat_id +
SABLE_TELEGRAM_BOT_TOKEN, or alert_configs.discord_webhook_url) alerts are still
*recorded* and surface in the SableWeb /ops alerts view.

Usage:
    python scripts/run_alerts.py [ORG_ID|_all]
"""
from __future__ import annotations

import sys

from sable_platform.db.connection import get_db
from sable_platform.workflows.alert_delivery import deliver_alerts_by_ids
from sable_platform.workflows.alert_evaluator import evaluate_alerts


def main() -> int:
    org = sys.argv[1] if len(sys.argv) > 1 else "_all"
    conn = get_db()
    try:
        # Evaluation is decoupled from delivery (per the alert-system contract):
        # create the alert rows + commit, then deliver after the commit.
        ids = evaluate_alerts(conn, org)
        conn.commit()
        delivered = 0
        if ids:
            delivered = deliver_alerts_by_ids(conn, ids) or 0
            conn.commit()
        print(f"alerts: org={org} created={len(ids)} delivered={delivered}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
