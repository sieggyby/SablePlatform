"""C2.2 shared-async-loop orchestration tests.

The loop runner sequences both transports on ONE event loop. These tests assert
the orchestration contract without a live PTB/discord lifecycle:

  - no transports configured → returns cleanly (no error)
  - a discord listener without a token raises (config error surfaced early)
  - both transports run as concurrent tasks on the same loop; a failure in one
    cancels the other and propagates
"""
from __future__ import annotations

import asyncio

import pytest

from sable_platform.relay.bot import loop as loop_mod


def test_no_transports_returns_cleanly() -> None:
    # Nothing configured → no-op, no exception.
    asyncio.run(loop_mod.run_listeners())


def test_discord_without_token_raises() -> None:
    class _FakeDiscord:
        pass

    with pytest.raises(ValueError):
        asyncio.run(
            loop_mod.run_listeners(
                discord_listener=_FakeDiscord(), discord_token=None
            )
        )


def test_failure_in_one_transport_cancels_other_and_propagates(monkeypatch) -> None:
    # Replace the per-transport runners with fakes so no network is touched:
    # the "telegram" task raises; the "discord" task would idle forever — it
    # must be cancelled and run_listeners must re-raise the telegram error.
    cancelled = {"discord": False}

    async def _boom_tg(_listener):
        raise RuntimeError("tg listener crashed")

    async def _idle_discord(_listener, _token):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled["discord"] = True
            raise

    monkeypatch.setattr(loop_mod, "_run_telegram", _boom_tg)
    monkeypatch.setattr(loop_mod, "_run_discord", _idle_discord)

    sentinel_tg = object()
    sentinel_dc = object()

    with pytest.raises(RuntimeError, match="tg listener crashed"):
        asyncio.run(
            loop_mod.run_listeners(
                telegram=sentinel_tg,
                discord_listener=sentinel_dc,
                discord_token="tok",
            )
        )
    assert cancelled["discord"] is True
