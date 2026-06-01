"""Reactive-register drafter (DESIGN §4 ``drafter/compose_reactive`` — C3.3).

The reactive HK-47 register composer — all hard refusals route here (SAFETY §0:
a refusal is charged content by definition, always reactive). Builds the reactive
cached system block + the delimiter-wrapped variable user prompt, calls the C3.1
:class:`~sable_platform.autocm.llm.LLMProvider` seam, parses the JSON draft, and
falls back to the deterministic vendored reactive render (R-4) on any ``None`` /
unparseable completion — and ALSO whenever the request is a hard refusal: a refusal
must NOT depend on a live LLM call, so a hard-refusal request uses the calibrated
deterministic refusal template directly (the safest, most predictable path for the
single most-sensitive output class).

NO real Anthropic / network call in tests — a deterministic FAKE provider returns a
recorded completion; the real adapter sets ``cache_control: ephemeral`` on the
system block.
"""
from __future__ import annotations

import logging

from sable_platform.autocm.classifier.register import REACTIVE
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


async def compose_reactive(
    request: DraftRequest, persona: NuloPersona, provider: LLMProvider
) -> DraftResult:
    """Compose a reactive-register reply (never raises — the seam contract).

    A HARD REFUSAL bypasses the LLM entirely and uses the calibrated deterministic
    reactive refusal template — the refusal wording is locked (VOICE §4 / SAFETY §1)
    and must never hinge on a live model call. Otherwise it builds the reactive
    cached system prefix + the delimiter-wrapped user prompt, calls the LLM seam, and
    parses the draft; a ``None`` / unparseable completion falls back to the
    deterministic vendored reactive render. ``used_llm=False`` marks any deterministic
    output for audit / cost.
    """
    # Hard refusals: deterministic calibrated refusal, no LLM (the locked-wording path).
    if request.is_refusal:
        return DraftResult(
            text=persona.render_fallback(request),
            register=REACTIVE,
            cited_chunk_ids=cited_chunk_ids(request),
            used_llm=False,
            reasoning="hard refusal — calibrated deterministic reactive template (no LLM)",
        )

    system = persona.system_block(REACTIVE)
    user_prompt = build_user_prompt(request)
    try:
        raw = await provider.complete(system, user_prompt, max_tokens=DRAFT_MAX_TOKENS)
    except Exception:  # pragma: no cover - defensive; the seam shouldn't raise
        logger.exception("compose_reactive provider.complete raised; deterministic fallback")
        raw = None

    parsed = parse_draft(raw, register=REACTIVE)
    if parsed is None:
        return DraftResult(
            text=persona.render_fallback(request),
            register=REACTIVE,
            cited_chunk_ids=cited_chunk_ids(request),
            used_llm=False,
            reasoning="deterministic R-4 fallback (no LLM draft)",
        )
    draft_text, _register, reasoning = parsed
    return DraftResult(
        text=draft_text,
        register=REACTIVE,
        cited_chunk_ids=cited_chunk_ids(request),
        used_llm=True,
        reasoning=reasoning,
    )


__all__ = ["compose_reactive"]
