"""Calm-register drafter (DESIGN §4 ``drafter/compose_calm`` — C3.3).

The calm Bill-Monday register composer. Builds the calm cached system block + the
delimiter-wrapped variable user prompt (thread context + KB facts + the message),
calls the injected :class:`~sable_platform.autocm.llm.LLMProvider` through the C3.1
seam, parses the JSON draft, and falls back to the deterministic vendored render
(R-4) on any ``None`` / unparseable completion. The register is fixed to calm here
— the C3.4b classifier already selected the register; the composer is chosen per
register (MEGAPLAN C3.3: "register comes from C3.4b").

NO real Anthropic / network call in tests — a deterministic FAKE provider returns a
recorded completion; the real adapter (selected by the manifest) sets
``cache_control: ephemeral`` on the system block.
"""
from __future__ import annotations

import logging

from sable_platform.autocm.classifier.register import CALM
from sable_platform.autocm.drafter.compose_shared import (
    build_user_prompt,
    cited_chunk_ids,
)
from sable_platform.autocm.drafter.persona import (
    DRAFT_MAX_TOKENS,
    DraftRequest,
    DraftResult,
    NuloPersona,
    parse_draft,
)
from sable_platform.autocm.llm import LLMProvider

logger = logging.getLogger(__name__)


async def compose_calm(
    request: DraftRequest, persona: NuloPersona, provider: LLMProvider
) -> DraftResult:
    """Compose a calm-register reply for ``request`` (never raises — the seam contract).

    Builds the calm cached system prefix + the delimiter-wrapped user prompt, calls
    the LLM seam, and parses the draft. On a ``None`` / unparseable completion (LLM
    disabled / budget exhausted / SDK failure / bad JSON) it falls back to the
    deterministic vendored render so the calm surface always produces an in-voice
    line (``used_llm=False`` marks the fallback for audit / cost).
    """
    system = persona.system_block(CALM)
    user_prompt = build_user_prompt(request)
    try:
        raw = await provider.complete(system, user_prompt, max_tokens=DRAFT_MAX_TOKENS)
    except Exception:  # pragma: no cover - defensive; the seam shouldn't raise
        logger.exception("compose_calm provider.complete raised; deterministic fallback")
        raw = None

    parsed = parse_draft(raw, register=CALM)
    if parsed is None:
        return DraftResult(
            text=persona.render_fallback(request),
            register=CALM,
            cited_chunk_ids=cited_chunk_ids(request),
            used_llm=False,
            reasoning="deterministic R-4 fallback (no LLM draft)",
        )
    draft_text, _register, reasoning = parsed
    # the calm composer pins the register to calm — the classifier chose it; an LLM
    # that tries to self-escalate to reactive here is ignored (register is fixed).
    return DraftResult(
        text=draft_text,
        register=CALM,
        cited_chunk_ids=cited_chunk_ids(request),
        used_llm=True,
        reasoning=reasoning,
    )


__all__ = ["compose_calm"]
