"""TG publisher (DESIGN §4 ``publisher/tg``).

SKELETON. Publishes an approved/auto-sent reply through the SableRelay
publish-exactly-once outbox (relay owns the transport; AutoCM never builds its own
TG client). C3.1 fixes the seam shape only.
"""
from __future__ import annotations

from typing import Protocol


class Publisher(Protocol):
    """Publish a reply to a chat via the SableRelay outbox (exactly-once)."""

    def publish(self, org_id: str, chat_id: str, text: str, *, reply_to: str | None = None) -> str:
        """Enqueue a reply on the relay outbox; return the outbox row handle."""
        ...


class NotImplementedTgPublisher:
    """Stub publisher — rides the relay outbox once that surface is wired."""

    def publish(self, org_id: str, chat_id: str, text: str, *, reply_to: str | None = None) -> str:
        raise NotImplementedError("TG publish over the relay outbox is wired in a later chunk")


__all__ = ["Publisher", "NotImplementedTgPublisher"]
