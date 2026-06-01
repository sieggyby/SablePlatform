"""Workflow: autocm_weekly_digest — weekly Monday "What Mattered" digest (C3.7).

The SP ``WorkflowRunner`` trigger for the DIGEST.md weekly digest (one of the four
``SABLE_PLATFORM_INTEGRATION §1`` durable/scheduled WorkflowRunner jobs). Scheduled
weekly (Monday, client timezone — the cron entry owns the timezone); generates the
A+C "What Mattered" digest over the prior complete week, then routes it per the
DIGEST §5 preview-vs-deliver gate (operator preview weeks 1–4, founder from week
5+). On a generation/delivery failure it raises the DIGEST §6 no-deliver alarm.

Two steps so generation and delivery are distinct durable units:

  * ``generate_digest``  — assemble the :class:`WeeklyDigestReport`, stash the body
    + numeric legs in the step output for visibility, write a
    ``weekly_digest_generated`` audit row;
  * ``deliver_digest``   — route via the injected :class:`DigestDelivery` seam
    (preview-vs-deliver), write a ``weekly_digest_delivered`` audit row.

Config:
  * ``client_id`` (optional) — a specific AutoCM client; otherwise the org's single
    AutoCM client (``autocm_clients.org_id == org_id``) is resolved.
  * ``week_start`` (optional ISO ``YYYY-MM-DD`` / ``...Z``) — the inclusive
    Monday-00:00 anchor of the digest week. An explicit value makes the window
    deterministic (the tests' FAKE CLOCK); a cron run with no value defaults to the
    prior complete week relative to wall clock.

**Delivery seam (no transport in JSON config).** ``DigestDelivery`` is an OBJECT
seam, not a JSON-config value, so the deliverer is obtained from the module-level
:func:`set_delivery_factory` hook (defaulting to :class:`NullDigestDelivery` — the
relay-backed transport is wired in C4.3). Tests install a FAKE factory; NO real
telegram / network runs here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import text

from sable_platform.autocm.digest import weekly
from sable_platform.autocm.digest.weekly import (
    ACTION_DIGEST_GENERATED,
    AUDIT_SOURCE,
    DigestDelivery,
)
from sable_platform.db.audit import log_audit
from sable_platform.errors import INVALID_CONFIG, SableError
from sable_platform.workflows import registry
from sable_platform.workflows.models import StepDefinition, StepResult, WorkflowDefinition

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Delivery factory hook (the OBJECT seam — kept out of JSON config)
# ---------------------------------------------------------------------------
class NullDigestDelivery:
    """No-op :class:`DigestDelivery` — records nothing, sends nothing.

    The production default until the C4.3 relay-backed transport is wired; the
    digest still GENERATES + routes (and audits the routing decision), it simply has
    no live outbound surface yet. Tests install a FAKE factory instead.
    """

    def to_operator(self, org_id: str, body: str) -> Optional[str]:  # noqa: ARG002
        return None

    def to_founder(self, org_id: str, body: str) -> Optional[str]:  # noqa: ARG002
        return None


_DELIVERY_FACTORY: Callable[[str], DigestDelivery] = lambda org_id: NullDigestDelivery()  # noqa: E731


def set_delivery_factory(factory: Callable[[str], DigestDelivery]) -> None:
    """Install the delivery factory the workflow uses (tests pass a fake).

    ``factory`` is called with the org_id and returns a :class:`DigestDelivery`. The
    production default is :class:`NullDigestDelivery` until C4.3 wires the
    relay-backed transport.
    """
    global _DELIVERY_FACTORY
    _DELIVERY_FACTORY = factory


def reset_delivery_factory() -> None:
    """Restore the default :class:`NullDigestDelivery` factory (test teardown)."""
    global _DELIVERY_FACTORY
    _DELIVERY_FACTORY = lambda org_id: NullDigestDelivery()  # noqa: E731


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sa_conn(ctx):
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


def _resolve_week_start(ctx) -> datetime:
    """Resolve the digest week anchor (config ``week_start`` wins; else prior week).

    An explicit ``week_start`` makes the window deterministic (the test FAKE CLOCK);
    a cron run with no value uses the start of the prior complete 7-day window
    relative to wall clock.
    """
    raw = ctx.config.get("week_start")
    if raw:
        parsed = _parse_iso(str(raw))
        if parsed is None:
            raise SableError(INVALID_CONFIG, f"bad week_start {raw!r} (want ISO date)")
        return parsed
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=7)


def _parse_iso(value: str) -> Optional[datetime]:
    raw = value.strip()
    if len(raw) == 10:  # YYYY-MM-DD
        raw = raw + "T00:00:00+00:00"
    elif raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
def _generate_digest(ctx) -> StepResult:
    conn = _sa_conn(ctx)
    client_id = _resolve_client_id(conn, ctx)
    week_start = _resolve_week_start(ctx)
    report = weekly.generate(conn, client_id, week_start)

    log_audit(
        conn,
        actor=AUDIT_SOURCE,
        action=ACTION_DIGEST_GENERATED,
        org_id=report.org_id,
        entity_id=str(client_id),
        detail={
            "client_id": client_id,
            "week_start": report.week_start,
            "minutes_saved": report.minutes_saved,
            "health_delta": report.health_delta,
            "sections": list(report.sections),
        },
        source=AUDIT_SOURCE,
    )
    conn.commit()
    return StepResult(
        "completed",
        {
            "client_id": client_id,
            "week_start": report.week_start,
            "week_end": report.week_end,
            "minutes_saved": report.minutes_saved,
            "health_delta": report.health_delta,
            "sections": list(report.sections),
            "body": report.body,
            "button_count": len(report.buttons),
        },
    )


def _deliver_digest(ctx) -> StepResult:
    conn = _sa_conn(ctx)
    client_id = _resolve_client_id(conn, ctx)
    week_start = _resolve_week_start(ctx)
    report = weekly.generate(conn, client_id, week_start)
    delivery = _DELIVERY_FACTORY(ctx.org_id)
    outcome = weekly.deliver(conn, report, delivery, week_start)
    conn.commit()
    return StepResult(
        "completed",
        {
            "client_id": client_id,
            "routed_to": outcome.routed_to,
            "deployment_week": outcome.deployment_week,
            "delivered_to_founder": outcome.founder_handle is not None,
            "delivered_to_operator": outcome.operator_handle is not None,
        },
    )


AUTOCM_WEEKLY_DIGEST = WorkflowDefinition(
    name="autocm_weekly_digest",
    version="1.0",
    steps=[
        StepDefinition(name="generate_digest", fn=_generate_digest, max_retries=1),
        StepDefinition(name="deliver_digest", fn=_deliver_digest, max_retries=1),
    ],
)

registry.register(AUTOCM_WEEKLY_DIGEST)
