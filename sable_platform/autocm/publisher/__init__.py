"""AutoCM publisher (DESIGN §4 ``publisher/``).

``tg`` (TG publish over the SableRelay outbox), ``x_reply`` (feature-flagged off
in v1). Skeletons; full impl rides the relay publish-exactly-once outbox.
"""
from __future__ import annotations

from .tg import NotImplementedTgPublisher, Publisher

__all__ = ["Publisher", "NotImplementedTgPublisher"]
