"""X reply publisher (DESIGN §4 ``publisher/x_reply``).

SKELETON. FEATURE-FLAGGED OFF in v1 (X track is a v2 feature, per DESIGN §4 +
X_INTEGRATION.md). Present so the seam exists; disabled by default.
"""
from __future__ import annotations

# v1 default: the X reply surface is OFF (DESIGN §4: "feature-flagged off in v1").
X_REPLY_ENABLED_DEFAULT = False


class XReplyPublisher:
    """Stub X-reply publisher — feature-flagged off in v1."""

    def __init__(self, *, enabled: bool = X_REPLY_ENABLED_DEFAULT) -> None:
        self.enabled = enabled

    def publish(self, org_id: str, tweet_id: str, text: str) -> str:
        raise NotImplementedError("X reply publishing is a feature-flagged v2 surface")


__all__ = ["XReplyPublisher", "X_REPLY_ENABLED_DEFAULT"]
