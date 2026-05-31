"""Protocol-only LLM seam for `core` — ZERO anthropic, zero network.

`core` is the deterministic engine: it never ships an LLM client. But downstream
consumers (SableAutoCM, C3.1) layer an optional LLM on top of the deterministic
surface (e.g. the `/review` color line, the scheduled dev-activity digest). So
`core` declares the SHAPE of that dependency as a `typing.Protocol` and nothing
more — the concrete Anthropic-using implementation is authored consumer-side and
injected, so `core` stays importable on a 3.9 venv with only pyyaml + httpx and
never grows an `anthropic` import.

This is a NET-NEW protocol-only addition, not a factoring of the standalone bot's
`sable_pulse/llm.py` (whose anthropic-using bodies stay bot-side and become the
AutoCM Anthropic-adapter reference in C3.1). `core` only ever sees an object that
*satisfies* `LLMProvider` — it never constructs one.
"""
from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """The minimal async text-generation seam `core` consumers implement.

    A `core` caller that wants optional LLM color accepts an `LLMProvider` and
    `await`s `complete(...)`; the deterministic path is always the fallback when
    the provider is absent or returns None. Implementations MUST be the only place
    an LLM SDK (anthropic/openai/…) is imported — never `core`.
    """

    async def complete(
        self,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 256,
        model: Optional[str] = None,
        stop: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Return generated text for (`system`, `prompt`), or None on failure/disabled.

        Returning None (never raising) lets the deterministic surface carry the
        output uninterrupted — the LLM is garnish, never the hot path.
        """
        ...
