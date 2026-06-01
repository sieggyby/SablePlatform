"""Long-running relay listener (the ``relay-bot`` deployment unit).

Entry point for the VPS ``sable-platform-relay-bot.service`` /
``relay-bot`` compose service (MEGAPLAN C4.3). This is the long-running
TG/Discord listener that ALSO hosts the in-process AutoCM online handlers
(per MEGAPLAN §2 runtime topology: AutoCM's online path — filter→classify→
draft→gate→HITL post — co-runs INSIDE ``relay-bot`` via the relay
listener handler-registry, not as a cross-process queue).

This script mirrors the ``scripts/run_alerts.py`` precedent: a thin, env-driven
supervisor entry that wires already-built, already-tested modules
(``relay.bot.loop.run_listeners`` + ``relay.bot.telegram_app.TelegramListener``
/ ``relay.bot.discord_app.DiscordListener``), adds NO schema, and carries NO
secret value (every credential is read from the process environment via
``RelaySettings`` — the ``RELAY_*`` env contract in ``relay/config.py``).

SINGLE-REPLICA INVARIANT (MEGAPLAN §2 / C4.3 prerequisite): ``relay-bot`` is
the SOLE unit that instantiates the in-memory core ``RateLimiter`` (the AutoCM
online path's per-process quota). The in-memory limiter is single-PROCESS
state, so ``relay-bot`` MUST run as EXACTLY ONE replica while it is the only
quota mechanism. Do NOT scale this unit to >1 replica without first promoting
the §8-deferred shared-store limiter (see OPERATIONS_RUNBOOK.md "Kill-switches
& scaling invariants"). The batch worker units (relay-poller / autocm batch)
MUST NOT instantiate the in-memory limiter — they govern spend via
``check_budget()`` / ``cost_events``.

Usage:
    SABLE_OPERATOR_ID=relay-bot python scripts/run_relay_bot.py

Required env (none are secrets-in-source — all read from the environment / .env):
    RELAY_TG_BOT_TOKEN          Telegram bot token (Telegram transport)
    RELAY_DISCORD_BOT_TOKEN     Discord bot token (Discord transport, optional)
    SABLE_DATABASE_URL          shared SP database URL (Postgres on the VPS)
    ANTHROPIC_API_KEY           AutoCM drafter/classifier LLM key (online path)

A transport whose token is absent is simply not run (``run_listeners`` accepts a
``None`` listener) — the listener decides at runtime, never at import time.
"""
from __future__ import annotations

import asyncio
import logging

from sable_platform.db.connection import get_db
from sable_platform.logging_config import configure_logging
from sable_platform.relay.bot.discord_app import DiscordListener
from sable_platform.relay.bot.loop import run_listeners
from sable_platform.relay.bot.telegram_app import TelegramListener
from sable_platform.relay.config import get_relay_settings

logger = logging.getLogger(__name__)


def _build_listeners(conn, settings):
    """Build the configured transports from env (token present => transport on)."""
    telegram = None
    discord_listener = None
    if settings.tg_bot_token:
        telegram = TelegramListener(conn, bot_token=settings.tg_bot_token)
        telegram.install_handlers()
        logger.info("relay-bot: telegram transport enabled")
    else:
        logger.warning("relay-bot: RELAY_TG_BOT_TOKEN unset; telegram transport off")
    if settings.discord_bot_token:
        discord_listener = DiscordListener(conn)
        logger.info("relay-bot: discord transport enabled")
    else:
        logger.warning("relay-bot: RELAY_DISCORD_BOT_TOKEN unset; discord transport off")
    return telegram, discord_listener, settings.discord_bot_token


def main() -> int:
    configure_logging()
    settings = get_relay_settings()
    conn = get_db()
    try:
        telegram, discord_listener, discord_token = _build_listeners(conn, settings)
        if telegram is None and discord_listener is None:
            logger.error(
                "relay-bot: no transports configured (set RELAY_TG_BOT_TOKEN "
                "and/or RELAY_DISCORD_BOT_TOKEN); nothing to run"
            )
            return 1
        # NOTE (MEGAPLAN §2): the in-process AutoCM online handler-registry
        # (filter→classify→draft→gate→HITL) and the SOLE in-memory core
        # RateLimiter are bootstrapped here, registered onto the listener's
        # C2.7 RelayHandlerRegistry. This unit is the only place that quota
        # state lives — keep it pinned to replica=1.
        asyncio.run(
            run_listeners(
                telegram=telegram,
                discord_listener=discord_listener,
                discord_token=discord_token,
            )
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
