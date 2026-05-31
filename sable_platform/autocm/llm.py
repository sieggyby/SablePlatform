"""LLM provider seam (MEGAPLAN C3.1 — seam 2 of 3).

The vendored core ships a **protocol-only** ``LLMProvider`` (``typing.Protocol``,
ZERO anthropic — D-1 import purity). Core only ever *accepts* an object satisfying
it; it never constructs one. So the concrete, config-driven adapter lives HERE in
the AutoCM layer — the ONLY place an LLM SDK is imported.

Seam shape (each seam has >=1 impl + >=1 stub):
  * :class:`AnthropicProvider` — the default impl (Anthropic Claude, the SDK
    imported LAZILY inside ``complete`` so importing this module never requires
    ``anthropic`` and never makes a network call at import time).
  * :class:`NullLLMProvider` — the stub: always returns ``None`` (the
    deterministic surface carries the output uninterrupted — the LLM is garnish,
    never the hot path; full C3.3 drafter behaviour lands later).

``build_llm_provider`` is the config-driven factory the deployment manifest
selects: ``provider="anthropic"`` (default) or ``provider="null"``. Future
providers register here without touching call sites.

``AnthropicProvider`` re-exports the vendored ``LLMProvider`` protocol so a single
``isinstance(provider, LLMProvider)`` check (runtime-checkable) proves any adapter
SATISFIES the core seam — the C3.1 exit assertion.
"""
from __future__ import annotations

import logging
from typing import List, Optional

# The protocol-only seam shipped by the vendored core (zero anthropic).
from sable_platform._vendor.sable_pulse_core import LLMProvider

logger = logging.getLogger(__name__)

# The v1 default model. Per claude-api guidance the C3.3 drafter MUST set
# prompt caching on the system block; that is a C3.3 concern — this seam just
# exposes the config-driven `model` passthrough.
DEFAULT_MODEL = "claude-opus-4-8"


class AnthropicProvider:
    """Default :class:`LLMProvider` impl over the Anthropic SDK.

    The ``anthropic`` import is deferred to first use so importing this module is
    dependency-free and side-effect-free (the AutoCM package can be imported on a
    process that never makes an LLM call). ``api_key=None`` resolves the key from
    the standard ``ANTHROPIC_API_KEY`` env var (secrets-in-env; never inline —
    see the manifest secrets invariant).
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = None  # lazily constructed

    def _ensure_client(self):
        if self._client is None:
            # Lazy import: the ONLY place the LLM SDK is imported (core never does).
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def complete(
        self,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 256,
        model: Optional[str] = None,
        stop: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Return generated text for (``system``, ``prompt``), or None on failure.

        Never raises (the seam contract): on any SDK / network / parse error it
        logs and returns ``None`` so the deterministic surface stays the hot path.
        The full drafter wiring (prompt-cache control on the system block, thread
        context, register selection) is C3.3 — this is the transport adapter.
        """
        try:
            client = self._ensure_client()
            kwargs: dict = {
                "model": model or self._model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            }
            if stop:
                kwargs["stop_sequences"] = stop
            resp = await client.messages.create(**kwargs)
            parts = [
                block.text
                for block in getattr(resp, "content", [])
                if getattr(block, "type", None) == "text"
            ]
            text = "".join(parts).strip()
            return text or None
        except Exception:  # pragma: no cover - network/SDK failure path
            logger.exception("AnthropicProvider.complete failed; returning None")
            return None


class NullLLMProvider:
    """Stub :class:`LLMProvider` that always returns ``None`` (no LLM).

    Used for deployments with the LLM disabled, for the C3.1 scaffolding tests,
    and as the budget-exhausted / hard-fail fallback target (the deterministic
    NULO surface then carries the reply — D-1/R-4). Satisfies the core protocol.
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
        return None


# Config-driven registry: manifest `llm.provider` selects the adapter.
_PROVIDERS = {
    "anthropic": AnthropicProvider,
    "null": NullLLMProvider,
}


def build_llm_provider(
    provider: str = "anthropic",
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> LLMProvider:
    """Build a config-selected :class:`LLMProvider` (default: Anthropic).

    ``provider`` is the manifest's ``llm.provider``. Unknown providers raise
    ``ValueError`` so a config typo fails loudly rather than silently disabling
    the LLM. The returned object satisfies the vendored-core ``LLMProvider``
    protocol (runtime-checkable).
    """
    key = provider.strip().lower()
    if key not in _PROVIDERS:
        raise ValueError(
            f"unknown LLM provider {provider!r}; expected one of {sorted(_PROVIDERS)}"
        )
    if key == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model or DEFAULT_MODEL)
    return NullLLMProvider()


__all__ = [
    "LLMProvider",
    "AnthropicProvider",
    "NullLLMProvider",
    "build_llm_provider",
    "DEFAULT_MODEL",
]
