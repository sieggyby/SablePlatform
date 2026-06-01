"""AutoCM batch worker — digest / KB-refresh / adversarial / auto-demotion sweep.

Entry point for the VPS ``sable-platform-autocm-batch.service`` /
``autocm-batch`` compose service (MEGAPLAN C4.3). This is the SEPARATE worker
unit (NOT the online path, which co-runs inside ``relay-bot``) that runs the
four scheduled AutoCM batch jobs — already-registered SP builtin workflows:

  * ``autocm_kb_refresh``        (C3.2c — re-fetch due KB sources)
  * ``autocm_autonomy_sweep``    (C3.5a — rolling-7d auto-demotion)
  * ``autocm_weekly_digest``     (C3.7  — weekly analytics digest)
  * ``autocm_adversarial_sweep`` (C3.9  — daily adversarial regression)

over every ENABLED AutoCM client (``autocm_clients.enabled=1``), via the SP
``WorkflowRunner`` + registry (the same path ``sable-platform workflow run``
uses). One job set per invocation; schedule it with systemd ``OnCalendar`` /
the compose loop / SP cron (the unit is a ``oneshot`` like the alerts timer).

COST-CONTROL INVARIANT (MEGAPLAN §2 / C4.3 prerequisite (a)): the batch worker
MUST NOT instantiate the in-memory core ``RateLimiter`` (that single-process
quota lives ONLY in ``relay-bot``). Batch LLM spend is governed by SP's
``check_budget()`` / ``cost_events`` ledger inside each workflow's adapter —
NEVER the per-process counter. This is what makes the multi-UNIT topology safe.

This mirrors ``scripts/run_alerts.py``: thin, env-driven, wires only committed +
tested modules, adds NO schema, and carries NO secret value.

Usage:
    SABLE_OPERATOR_ID=autocm-batch python scripts/run_autocm_batch.py [WORKFLOW ...]

With no args, runs all four batch workflows for every enabled AutoCM client.
With explicit workflow names, runs only those (e.g. just the weekly digest on a
weekly timer):
    python scripts/run_autocm_batch.py autocm_weekly_digest

Required env (none are secrets-in-source — all read from the environment / .env):
    SABLE_DATABASE_URL   shared SP database URL (Postgres on the VPS)
    ANTHROPIC_API_KEY    AutoCM LLM key (drafter/classifier/adversarial)
"""
from __future__ import annotations

import logging
import sys

from sqlalchemy import text

from sable_platform.db.connection import get_db
from sable_platform.logging_config import configure_logging
from sable_platform.workflows import registry
from sable_platform.workflows.engine import WorkflowRunner

logger = logging.getLogger(__name__)

# The four C4.3 batch jobs (online path is hosted in relay-bot, NOT here).
BATCH_WORKFLOWS = (
    "autocm_kb_refresh",
    "autocm_autonomy_sweep",
    "autocm_weekly_digest",
    "autocm_adversarial_sweep",
)


def _enabled_autocm_orgs(conn) -> list[str]:
    """Org ids of every ENABLED AutoCM client (deterministic order)."""
    rows = conn.execute(
        text("SELECT org_id FROM autocm_clients WHERE enabled = 1 ORDER BY org_id")
    ).fetchall()
    return [r[0] for r in rows]


def _run_one(conn, workflow_name: str, org_id: str) -> bool:
    """Run a single batch workflow for one org. Returns True on success."""
    try:
        defn = registry.get(workflow_name)
        runner = WorkflowRunner(defn)
        run_id = runner.run(org_id, {}, conn=conn)
        conn.commit()
        logger.info(
            "autocm-batch: ran %s for org=%s (run_id=%s)", workflow_name, org_id, run_id
        )
        return True
    except Exception:  # noqa: BLE001 — one failing job/org must not abort the rest
        conn.rollback()
        logger.exception(
            "autocm-batch: %s FAILED for org=%s", workflow_name, org_id
        )
        return False


def main() -> int:
    configure_logging()
    requested = sys.argv[1:]
    workflows = requested or list(BATCH_WORKFLOWS)
    unknown = [w for w in workflows if w not in BATCH_WORKFLOWS]
    if unknown:
        logger.error(
            "autocm-batch: unknown workflow(s) %s; valid: %s",
            unknown,
            list(BATCH_WORKFLOWS),
        )
        return 2

    conn = get_db()
    try:
        orgs = _enabled_autocm_orgs(conn)
        if not orgs:
            logger.info("autocm-batch: no enabled AutoCM clients; nothing to run")
            return 0
        failures = 0
        for org_id in orgs:
            for workflow_name in workflows:
                if not _run_one(conn, workflow_name, org_id):
                    failures += 1
        logger.info(
            "autocm-batch: done (orgs=%d, workflows=%d, failures=%d)",
            len(orgs),
            len(workflows),
            failures,
        )
        return 1 if failures else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
