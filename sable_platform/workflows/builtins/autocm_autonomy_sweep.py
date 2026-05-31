"""Workflow: autocm_autonomy_sweep — rolling-7d auto-demotion (C3.5a / DESIGN §7).

The SP ``WorkflowRunner`` trigger for DESIGN §7 auto-demotion trigger (1): the
rolling-7d clean-approval ``< 0.85`` sweep (one of the four
``SABLE_PLATFORM_INTEGRATION §1`` durable/scheduled WorkflowRunner jobs). Run on a
schedule (cron) per client; for every ``autocm_category_state`` row currently in
``state='auto'``, it recomputes the rolling-7d clean-approval rate and flips any
category below the 0.85 threshold back to ``hitl`` (no operator action), writing
an ``autonomy_auto_demoted_rolling7d`` audit row per demotion — so autonomy is
bidirectional, not promote-only.

Co-located with the rest of the autonomy machine (``gate/autonomy``): it shares
the ``autocm_category_state`` read path with ``gate/confidence`` and computes the
same ``clean_approval_rate`` quantity the C3.5a chunk already owns (the lone
DESIGN §7 scheduled job NOT given its own MEGAPLAN chunk — recorded decision).

Single step (``sweep_due_demotions``) so the sweep is the workflow's one durable
unit. The :func:`~sable_platform.autocm.gate.autonomy.sweep_auto_demotions` clock
defaults to wall-clock in production; tests drive a deterministic ``now`` directly
against the function (this wrapper is exercised end-to-end with a real autocm
client + an `auto` category with a failing rolling window).

Config:
  * ``client_id`` (optional) — sweep a specific AutoCM client; otherwise the org's
    single AutoCM client (``autocm_clients.org_id == org_id``) is resolved.
  * ``min_samples`` (optional, default 1) — a category with fewer than this many
    reviews in the 7d window is left alone (too little signal to demote on).
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from sable_platform.autocm.gate.autonomy import sweep_auto_demotions
from sable_platform.errors import INVALID_CONFIG, SableError
from sable_platform.workflows import registry
from sable_platform.workflows.models import StepDefinition, StepResult, WorkflowDefinition

log = logging.getLogger(__name__)


def _sa_conn(ctx):
    """Return the raw SQLAlchemy Connection backing the workflow ``ctx.db``.

    Builtin workflows receive a ``CompatConnection`` (sqlite3-style shim); the
    AutoCM gate modules speak native SQLAlchemy ``text()`` over a ``Connection``.
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


def _sweep_due_demotions(ctx) -> StepResult:
    conn = _sa_conn(ctx)
    client_id = _resolve_client_id(conn, ctx)
    min_samples = int(ctx.config.get("min_samples", 1))
    demoted = sweep_auto_demotions(
        conn, client_id, org_id=ctx.org_id, min_samples=min_samples
    )
    conn.commit()
    return StepResult(
        "completed",
        {
            "client_id": client_id,
            "demoted_count": len(demoted),
            "demoted_categories": [d.category for d in demoted],
        },
    )


AUTOCM_AUTONOMY_SWEEP = WorkflowDefinition(
    name="autocm_autonomy_sweep",
    version="1.0",
    steps=[
        StepDefinition(name="sweep_due_demotions", fn=_sweep_due_demotions, max_retries=1),
    ],
)

registry.register(AUTOCM_AUTONOMY_SWEEP)
