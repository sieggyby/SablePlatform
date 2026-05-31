"""Workflow: autocm_adversarial_sweep — daily adversarial regression (C3.9).

The SP ``WorkflowRunner`` trigger for the SAFETY §2 daily adversarial regression
harness (one of the four ``SABLE_PLATFORM_INTEGRATION §1`` durable/scheduled
WorkflowRunner jobs). Scheduled DAILY (cron) per client; runs the C3.9 battery —
prompt-injection (incl. thread-context-poisoning + author-tag-injection),
voice-drift, and hard-refusal-bypass cases — against the LIVE pipeline, records ONE
``autocm_adversarial_runs`` row, and persists an ``injection_blocked`` audit row for
every blocked injection (the encounter is on the record even though nothing was
published — the C3.9 exit).

Single step (``run_adversarial_battery``) so the daily run is the workflow's one
durable unit. The harness touches NO live LLM / telegram / network — the injection +
bypass suites run over the deterministic vendored ``check_refusal`` bank and the
voice-drift suite over the pure C3.3 register dispatch; tests drive a deterministic
``now`` via the injected clock.

Config:
  * ``client_id`` (optional) — a specific AutoCM client; otherwise the org's single
    AutoCM client (``autocm_clients.org_id == org_id``) is resolved.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from sable_platform.autocm.adversarial.regression import LivePipelineAdversarialHarness
from sable_platform.errors import INVALID_CONFIG, SableError
from sable_platform.workflows import registry
from sable_platform.workflows.models import StepDefinition, StepResult, WorkflowDefinition

log = logging.getLogger(__name__)


def _sa_conn(ctx):
    """Return the raw SQLAlchemy Connection backing the workflow ``ctx.db``.

    Builtin workflows receive a ``CompatConnection`` (sqlite3-style shim); the
    AutoCM harness speaks native SQLAlchemy ``text()`` over a ``Connection``.
    ``CompatConnection._conn`` is that underlying SA connection.
    """
    db = ctx.db
    return getattr(db, "_conn", db)


def _resolve_client_id(conn, ctx) -> int:
    explicit = ctx.config.get("client_id")
    if explicit is not None:
        return int(explicit)
    row = conn.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = :o"),
        {"o": ctx.org_id},
    ).fetchone()
    if row is None:
        raise SableError(
            INVALID_CONFIG,
            f"no AutoCM client for org '{ctx.org_id}' (seed autocm_clients first)",
        )
    return int(row[0])


def _run_adversarial_battery(ctx) -> StepResult:
    conn = _sa_conn(ctx)
    client_id = _resolve_client_id(conn, ctx)
    harness = LivePipelineAdversarialHarness(conn)
    result = harness.run_daily(client_id)
    conn.commit()
    return StepResult(
        "completed",
        {
            "client_id": client_id,
            "run_id": result.run_id,
            "status": result.status,
            "passed": result.passed,
            "failed": result.failed,
            "total": result.total,
            "failures": [c.name for c in result.cases if not c.passed],
        },
    )


AUTOCM_ADVERSARIAL_SWEEP = WorkflowDefinition(
    name="autocm_adversarial_sweep",
    version="1.0",
    steps=[
        StepDefinition(name="run_adversarial_battery", fn=_run_adversarial_battery, max_retries=1),
    ],
)

registry.register(AUTOCM_ADVERSARIAL_SWEEP)
