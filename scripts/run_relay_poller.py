"""Long-running relay X-timeline poller + publisher drain (the ``relay-poller`` unit).

Entry point for the VPS ``sable-platform-relay-poller.service`` /
``relay-poller`` compose service (MEGAPLAN C4.3). This is the background worker
(SableRelay/PLAN §7) that runs SEPARATELY from the listener (``relay-bot``):

  * Flow A timeline poll      — ``relay.feed.poller.poll_all_enabled``
  * sweeper (expiry/recon/GC) — ``relay.feed.sweeper.run_sweep``
  * publish drain (outbox)    — ``relay.feed.publisher.drain_due_jobs``

per loop tick, then sleeps ``RELAY_POLLER_INTERVAL_SECONDS`` (default 60s).

COST-CONTROL INVARIANT (MEGAPLAN §2 / C4.3 prerequisite (a)): this worker unit
MUST NOT instantiate the in-memory core ``RateLimiter`` (that single-process
quota lives ONLY in ``relay-bot``). Poller spend is governed by SocialData's
per-org daily cap (``check_daily_socialdata_budget``, enforced inside
``poll_org``) and SP's ``cost_events`` ledger — never the per-process counter.
This separation is what keeps the multi-UNIT topology from silently breaking
the cost guarantee, so this unit may be scaled independently of ``relay-bot``.

This mirrors ``scripts/run_alerts.py``: thin, env-driven, wires only committed +
tested modules, adds NO schema, and carries NO secret value (every credential is
read from the process environment).

TRANSPORT SEAMS (operator-provided, NOT secrets-in-source): the poller needs a
``SocialDataClient`` (its injectable ``http_get`` is the SocialData HTTP
transport, keyed by ``SOCIALDATA_API_KEY``) and the publisher/sweeper need a
``Sender`` (the TG/Discord send transport). These production transports are the
final wiring step before the relay feed goes live (RELAY.md: "feed mirroring not
built"); until they are wired, this entry FAILS LOUDLY rather than silently
no-op'ing a relay-poller that publishes nothing. Build the transports, then
return them from ``_build_socialdata_client`` / ``_build_sender``.

Usage:
    SABLE_OPERATOR_ID=relay-poller python scripts/run_relay_poller.py

Required env (none are secrets-in-source — all read from the environment / .env):
    SABLE_DATABASE_URL              shared SP database URL (Postgres on the VPS)
    SOCIALDATA_API_KEY              SocialData transport key (Flow A poll)
    RELAY_TG_BOT_TOKEN              Telegram send transport (publisher)
    RELAY_POLLER_INTERVAL_SECONDS   loop interval (optional; default 60)
"""
from __future__ import annotations

import logging
import os
import time

from sable_platform.db.connection import get_db
from sable_platform.logging_config import configure_logging
from sable_platform.relay.feed.poller import poll_all_enabled
from sable_platform.relay.feed.publisher import drain_due_jobs
from sable_platform.relay.feed.sweeper import run_sweep

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 60


def _interval_seconds() -> float:
    raw = os.environ.get("RELAY_POLLER_INTERVAL_SECONDS")
    if not raw:
        return float(DEFAULT_INTERVAL_SECONDS)
    try:
        return max(1.0, float(raw))
    except ValueError:
        logger.warning(
            "relay-poller: bad RELAY_POLLER_INTERVAL_SECONDS=%r; using %ss",
            raw,
            DEFAULT_INTERVAL_SECONDS,
        )
        return float(DEFAULT_INTERVAL_SECONDS)


def _build_socialdata_client(conn):
    """Construct the live SocialDataClient (Flow A poll transport).

    The injectable ``http_get`` is the SocialData HTTP transport, keyed by
    ``SOCIALDATA_API_KEY`` (secrets-in-env). This is the operator-provided
    production transport seam — wire it here when the relay feed goes live.
    """
    raise NotImplementedError(
        "relay-poller: SocialData HTTP transport not wired. Provide a "
        "SocialDataClient (http_get keyed by SOCIALDATA_API_KEY) here before "
        "enabling the relay-poller unit. See OPERATIONS_RUNBOOK.md "
        "'relay-poller — transport seams'."
    )


def _build_sender():
    """Construct the live TG/Discord Sender (publisher/sweeper send transport).

    This is the operator-provided production transport seam — wire it here when
    the relay feed goes live (RELAY.md: feed mirroring not yet built).
    """
    raise NotImplementedError(
        "relay-poller: send transport (Sender) not wired. Provide a Sender "
        "(TG/Discord send transport) here before enabling the relay-poller "
        "unit. See OPERATIONS_RUNBOOK.md 'relay-poller — transport seams'."
    )


def _tick(conn, sd_client, sender) -> None:
    """One poll → sweep → drain cycle (all committed + tested feed functions)."""
    polls = poll_all_enabled(conn, sd_client)
    conn.commit()
    sweep = run_sweep(conn, sender)
    conn.commit()
    drained = drain_due_jobs(conn, sender, sd_client=sd_client)
    conn.commit()
    logger.info(
        "relay-poller tick: polled=%d expired=%d published=%d",
        len(polls),
        sweep.submissions_expired,
        len(drained),
    )


def main() -> int:
    configure_logging()
    interval = _interval_seconds()
    conn = get_db()
    try:
        sd_client = _build_socialdata_client(conn)
        sender = _build_sender()
        logger.info("relay-poller: starting loop (interval=%ss)", interval)
        while True:
            try:
                _tick(conn, sd_client, sender)
            except Exception:  # noqa: BLE001 — one bad tick must not kill the loop
                logger.exception("relay-poller: tick failed; continuing")
            time.sleep(interval)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
