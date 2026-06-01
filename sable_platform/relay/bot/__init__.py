"""SableRelay listener / low-level dispatch primitive (MEGAPLAN C2.2).

This package is the **low-level dispatch primitive** half of the SableRelay
listener split:

  * ``txn``          — the ``BEGIN IMMEDIATE`` transaction helper (SQLite
                        exclusive-write-lock; Postgres serializable). The hard
                        invariant: **no external API call inside any
                        ``BEGIN IMMEDIATE``** (PLAN §3.1).
  * ``dedupe``       — the persistent, restart-safe ``relay_processed_updates``
                        dedupe gate (PLAN §3.1 step 1 / §3.3).
  * ``escaping``     — §15.2 output escaping / accidental-ping prevention
                        (Discord ``AllowedMentions.none()`` + escape;
                        Telegram HTML mode + ``html.escape`` + tag whitelist).
  * ``binding``      — §15.3 chat-binding lifecycle (TG ``migrate_to_chat_id``
                        re-point, bot-kicked / ``my_chat_member`` cleanup,
                        Discord 403/404 binding-flip), each in one
                        ``BEGIN IMMEDIATE``.
  * ``loop``         — the single shared ``asyncio`` event loop that runs the
                        Telegram PTB ``Application`` and the discord.py
                        ``Client`` concurrently.
  * ``telegram_app`` — the PTB ``Application`` + per-update routing.
  * ``discord_app``  — the discord.py ``Client`` + 3s-defer interaction
                        pattern.

The **AutoCM-facing handler-REGISTRATION API** (register a per-message handler
at boot + dispatch member-JOIN/leave to registered consumers, plus operator-chat
provisioning, the TG typing-indicator, and inline-button callback routing) is
**C2.7** and lives in ``registry`` (it is built ON TOP of C2.2's primitives, not
inside them). C2.2 stops at the per-update routing inside the async loop; the
registry sits above it.
"""
from __future__ import annotations

__all__ = [
    "immediate_txn",
    "Deduper",
    "mark_processed",
    # C2.7 AutoCM-facing registration API.
    "RelayHandlerRegistry",
    "RelayConsumer",
    "InboundMessage",
    "MemberEvent",
    "CallbackEvent",
    "CommandEvent",
    "TypingIndicator",
    "build_registry",
    "parse_command",
]

_REGISTRY_EXPORTS = (
    "RelayHandlerRegistry",
    "RelayConsumer",
    "InboundMessage",
    "MemberEvent",
    "CallbackEvent",
    "CommandEvent",
    "TypingIndicator",
    "build_registry",
    "parse_command",
)


def __getattr__(name: str):
    # Lazy re-exports so ``import sable_platform.relay.bot`` does not force the
    # telegram / discord imports unless a caller actually asks for them.
    if name in ("immediate_txn",):
        from sable_platform.relay.bot.txn import immediate_txn

        return immediate_txn
    if name in ("Deduper", "mark_processed"):
        from sable_platform.relay.bot.dedupe import Deduper, mark_processed

        return {"Deduper": Deduper, "mark_processed": mark_processed}[name]
    if name in _REGISTRY_EXPORTS:
        from sable_platform.relay.bot import registry as _registry

        return getattr(_registry, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
