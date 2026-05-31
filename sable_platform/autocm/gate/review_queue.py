"""HITL review surface (DESIGN §4 ``gate/review_queue``) — seam 1 of 3.

The ``HITLReviewSurface`` interface is the productization seam (Q12): the v1
white-glove tier posts review items to the per-client TG operator chat
(:class:`TelegramReviewSurface`); a managed/self-host tier swaps in a web dashboard
(:class:`WebDashboardReviewSurface`, stub) WITHOUT a rewrite of the pipeline — the
gate/confidence stage only ever talks to the abstract surface.

The TG impl RIDES the C2.7 relay primitives:
  * ``RelayHandlerRegistry.get_operator_chat`` / ``provision_operator_chat`` —
    resolve / provision the per-client operator chat (the HITL surface DB side).
  * the relay outbox / send path posts the review message with inline
    [Approve][Edit][Reject][Punt-to-founder] buttons; the inline-button callbacks
    route back via C2.7's callback router (full review-queue flow = C3.5b).

C3.1 fixes the interface + the TG impl's surface RESOLUTION over C2.7 + the
web-dashboard stub. The actual button POST + callback wiring is C3.5b.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from sable_platform.relay.bot.registry import RelayHandlerRegistry

# The HITL review actions an operator can take (HITL_UX §1).
REVIEW_ACTIONS = ("approve", "edit", "reject", "punt")


@dataclass(frozen=True)
class ReviewItem:
    """One draft posted to the HITL surface for an operator decision.

    Carries the SAFETY §5 audit field set the review must persist on decision
    (source message, cited chunks, draft text, category/tier/confidence).
    """

    draft_id: int
    org_id: str
    source_message_row_id: int
    draft_text: str
    category: str
    tier: int
    confidence: float
    register: str = "calm"
    cited_chunk_ids: List[int] = field(default_factory=list)


class HITLReviewSurface(ABC):
    """The seam: post a :class:`ReviewItem` to the human-in-the-loop surface.

    Implementations: :class:`TelegramReviewSurface` (v1, over C2.7) and
    :class:`WebDashboardReviewSurface` (v2 stub). The pipeline (C3.5b) depends on
    this ABC only — the surface is config-selected per deployment tier.
    """

    @abstractmethod
    def post_review(self, item: ReviewItem) -> str:
        """Post a review item; return a surface-specific handle (e.g. TG message id).

        Raises if the surface is not provisioned (rather than silently dropping the
        HITL queue — SAFETY §5 observability).
        """
        ...

    @abstractmethod
    def is_available(self, org_id: str) -> bool:
        """True iff the surface is provisioned/reachable for this client."""
        ...


class TelegramReviewSurface(HITLReviewSurface):
    """v1 HITL surface: the per-client TG operator chat, over the C2.7 registry.

    Resolves the operator chat through :class:`RelayHandlerRegistry`
    (``get_operator_chat`` / ``provision_operator_chat``) — the C2.7 HITL surface
    primitive. ``post_review`` posts the review message with inline buttons via the
    relay send path; the inline-button callbacks route back through C2.7's callback
    router (full button/callback wiring = C3.5b). C3.1 wires the surface RESOLUTION.
    """

    def __init__(self, registry: RelayHandlerRegistry, *, platform: str = "telegram") -> None:
        self._registry = registry
        self._platform = platform

    def ensure_provisioned(self, org_id: str, chat_id: str, *, title: Optional[str] = None) -> str:
        """Idempotently provision the operator chat (C2.7 ``provision_operator_chat``)."""
        return self._registry.provision_operator_chat(
            org_id, chat_id, platform=self._platform, title=title
        )

    def is_available(self, org_id: str) -> bool:
        return self._registry.get_operator_chat(org_id, platform=self._platform) is not None

    def post_review(self, item: ReviewItem) -> str:
        chat_id = self._registry.get_operator_chat(item.org_id, platform=self._platform)
        if chat_id is None:
            raise RuntimeError(
                f"HITL operator chat not provisioned for org {item.org_id!r} "
                f"(platform={self._platform}); provision it before queuing reviews"
            )
        # The inline-button POST + [Approve]/[Edit]/[Reject]/[Punt] callback wiring
        # over the relay outbox / C2.7 callback router lands in C3.5b. C3.1 wires
        # the surface resolution; the actual send is C3.5b.
        raise NotImplementedError(
            "review-message POST (inline buttons + callback routing) lands in C3.5b"
        )


class WebDashboardReviewSurface(HITLReviewSurface):
    """v2 HITL surface STUB: a managed/self-host web dashboard.

    Demonstrates the seam swaps cleanly to a non-TG surface without a pipeline
    rewrite. Not built in v1 — every method raises. Present so the seam has a
    second (stub) impl and the interface test can assert both satisfy the ABC.
    """

    def __init__(self, base_url: Optional[str] = None) -> None:
        self._base_url = base_url

    def is_available(self, org_id: str) -> bool:
        return False

    def post_review(self, item: ReviewItem) -> str:
        raise NotImplementedError("web-dashboard HITL surface is a v2 tier (stub)")


__all__ = [
    "HITLReviewSurface",
    "ReviewItem",
    "TelegramReviewSurface",
    "WebDashboardReviewSurface",
    "REVIEW_ACTIONS",
]
