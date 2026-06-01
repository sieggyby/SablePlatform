"""X reply publisher (DESIGN §4 ``publisher/x_reply``).

**FEATURE-FLAGGED OFF in v1.** The X reply surface is a v2 feature (DESIGN §4 +
X_INTEGRATION.md): v1 ships TG-only. The structural code is present so the seam
exists and v2 can land without a rewrite, but it is UNREACHABLE while the
per-client ``x-enable`` flag is off — :func:`enqueue_x_reply` and
:meth:`XReplyPublisher.publish` RAISE :class:`XReplyDisabled` unless the caller
passes ``enabled=True`` (which no v1 deployment does — the default is off).

Like the TG publisher (C3.6 ``tg.py``), the v2 design will ENQUEUE to the relay
outbox, never call the X transport directly. That wiring is deferred; today the
only guarantee asserted is that the path cannot be reached while disabled.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.engine import Connection

# v1 default: the X reply surface is OFF (DESIGN §4: "feature-flagged off in v1").
# The per-client enable flag lives in the deployment manifest; v1 never sets it.
X_REPLY_ENABLED_DEFAULT = False


class XReplyDisabled(RuntimeError):
    """Raised when an X-reply enqueue is attempted while the feature flag is off.

    The v1 floor: the X reply surface is structurally present but UNREACHABLE. A
    test asserts that calling into it while disabled raises this, so the feature
    flag is a hard gate, not advisory.
    """


def x_reply_enabled(*, enabled: bool = X_REPLY_ENABLED_DEFAULT) -> bool:
    """Whether the X reply surface is enabled (v1 default: ``False``)."""
    return bool(enabled)


def enqueue_x_reply(
    conn: Connection,
    draft_id: int,
    *,
    enabled: bool = X_REPLY_ENABLED_DEFAULT,
) -> None:
    """Enqueue an X reply onto the relay outbox (v2 — STRUCTURAL ONLY).

    Mirrors the C3.6 TG enqueue shape (outbox-only, never a direct X transport
    call) so v2 can fill in the body without a rewrite. UNREACHABLE in v1: raises
    :class:`XReplyDisabled` unless ``enabled=True``, which no v1 deployment passes.
    """
    if not x_reply_enabled(enabled=enabled):
        raise XReplyDisabled(
            "X reply publishing is a feature-flagged v2 surface — disabled in v1"
        )
    # v2 implementation goes here (outbox enqueue, parallel to publish_approved_draft).
    raise NotImplementedError("X reply enqueue is a v2 feature; structural seam only")


class XReplyPublisher:
    """Stub X-reply publisher — feature-flagged off in v1 (unreachable while off)."""

    def __init__(self, *, enabled: bool = X_REPLY_ENABLED_DEFAULT) -> None:
        self.enabled = bool(enabled)

    def publish(self, org_id: str, tweet_id: str, text: str, *, reply_to: Optional[str] = None) -> str:
        if not self.enabled:
            raise XReplyDisabled(
                "X reply publishing is a feature-flagged v2 surface — disabled in v1"
            )
        raise NotImplementedError("X reply publishing is a v2 feature; structural seam only")


__all__ = [
    "XReplyPublisher",
    "XReplyDisabled",
    "enqueue_x_reply",
    "x_reply_enabled",
    "X_REPLY_ENABLED_DEFAULT",
]
