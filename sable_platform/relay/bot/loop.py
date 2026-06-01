"""The single shared async event loop for both transports (MEGAPLAN C2.2).

The Telegram PTB ``Application`` and the discord.py ``Client`` are both
``asyncio`` apps. Per C2.2 they share **ONE** event loop in a single process
(the listener process) rather than spinning up two loops/threads — this keeps
the dedupe gate and the chat-binding lifecycle running against one DB
connection with no cross-thread contention.

This module owns the orchestration of that single loop:

  * :func:`run_listeners` — start both transports concurrently on the current
    running loop and await them together (cancelling both cleanly on shutdown).
  * It is transport-agnostic: it takes already-built :class:`TelegramListener`
    / :class:`DiscordListener` (either may be ``None`` when its token is not
    configured — the listener decides at runtime, not import time, which
    transports to run, exactly like ``config.py`` documents).

The per-update routing logic lives in ``telegram_app`` / ``discord_app``; this
module only sequences their lifecycles on the shared loop.
"""
from __future__ import annotations

import asyncio
import logging

from sable_platform.relay.bot.discord_app import DiscordListener
from sable_platform.relay.bot.telegram_app import ALLOWED_UPDATES, TelegramListener

logger = logging.getLogger(__name__)


async def _run_telegram(listener: TelegramListener) -> None:
    """Run the PTB Application's polling lifecycle on the shared loop.

    Uses the manual ``initialize`` / ``start`` / ``updater.start_polling``
    lifecycle (rather than the blocking ``run_polling``) so it can share the
    loop with the Discord client. Crucially, ``start_polling`` is invoked with
    ``allowed_updates=list(ALLOWED_UPDATES)`` so reactions + ``my_chat_member``
    are actually delivered (PLAN §7 item 7).
    """
    app = listener.app
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=list(ALLOWED_UPDATES))
    try:
        # Idle until cancelled.
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


async def _run_discord(listener: DiscordListener, token: str) -> None:
    """Run the discord.py client's connection lifecycle on the shared loop."""
    client = listener.client
    try:
        await client.start(token)
    finally:
        if not client.is_closed():
            await client.close()


async def run_listeners(
    *,
    telegram: TelegramListener | None = None,
    discord_listener: DiscordListener | None = None,
    discord_token: str | None = None,
) -> None:
    """Run the configured transports concurrently on ONE event loop.

    Either transport may be absent (its token unconfigured). Both run as tasks
    on the current running loop; if one raises, the other is cancelled and the
    exception propagates so the supervisor can restart the process.
    """
    tasks: list[asyncio.Task] = []
    if telegram is not None:
        tasks.append(asyncio.create_task(_run_telegram(telegram), name="relay-telegram"))
    if discord_listener is not None:
        if not discord_token:
            raise ValueError("discord_listener provided without discord_token")
        tasks.append(
            asyncio.create_task(
                _run_discord(discord_listener, discord_token), name="relay-discord"
            )
        )
    if not tasks:
        logger.warning("relay run_listeners: no transports configured; nothing to run")
        return

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    # Re-raise the first exception if a task failed.
    for task in done:
        exc = task.exception()
        if exc is not None:
            raise exc
