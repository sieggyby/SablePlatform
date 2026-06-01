"""AutoCM publisher (DESIGN §4 ``publisher/``).

``tg`` (the C3.6 ``[Approve]`` → relay-outbox enqueue — reads a C3.5b-approved
draft and enqueues exactly ONE ``relay_publication_jobs`` row; NEVER calls a
transport directly), ``x_reply`` (feature-flagged off in v1, structural only).
"""
from __future__ import annotations

from .tg import (
    NotImplementedTgPublisher,
    PublishEnqueueResult,
    Publisher,
    carrier_x_id,
    publish_approved_draft,
    publish_pending_approved,
)
from .x_reply import XReplyDisabled, XReplyPublisher, enqueue_x_reply, x_reply_enabled

__all__ = [
    "Publisher",
    "NotImplementedTgPublisher",
    "PublishEnqueueResult",
    "publish_approved_draft",
    "publish_pending_approved",
    "carrier_x_id",
    "XReplyPublisher",
    "XReplyDisabled",
    "enqueue_x_reply",
    "x_reply_enabled",
]
