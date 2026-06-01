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

**Cost tracking (MEGAPLAN C3.10).** The ``AnthropicProvider`` is the ONLY place an
LLM SDK + a real token-usage object (``resp.usage``) live, so it is where per-client
spend is captured. When the adapter is constructed with a
:class:`~sable_platform.autocm.cost.CostAccountant` and a ``client_id``, every
successful ``complete`` records the call's token usage into the accountant as a
SIDE EFFECT, keyed by ``client_id`` (per-client, no cross-client bleed). This does
NOT change the ``LLMProvider`` protocol's ``Optional[str]`` return type — the
recording is a pure side effect that does not alter the returned completion. The
:class:`NullLLMProvider` makes no API call and therefore records NOTHING. The
DB-persisted ``cost_events`` ledger is a deferred post-merge migration — see
``cost.py`` (the branch is frozen at migration head 058).
"""
from __future__ import annotations

import logging
from typing import List, Optional

# The protocol-only seam shipped by the vendored core (zero anthropic).
from sable_platform._vendor.sable_pulse_core import LLMProvider
from sable_platform.autocm.cost import CostAccountant

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

    **Cost capture (C3.10).** When ``accountant`` (a
    :class:`~sable_platform.autocm.cost.CostAccountant`) and ``client_id`` are
    supplied, each successful :meth:`complete` records that call's token usage into
    the accountant keyed by ``client_id`` — a SIDE EFFECT that leaves the
    ``Optional[str]`` return contract untouched. With no accountant the adapter
    behaves exactly as before (cost capture is opt-in; the online handler wires it).
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        accountant: Optional[CostAccountant] = None,
        client_id: Optional[int] = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = None  # lazily constructed
        # C3.10 per-client cost capture (opt-in; the only place token usage lives).
        self._accountant = accountant
        self._client_id = client_id

    def _ensure_client(self):
        if self._client is None:
            # Lazy import: the ONLY place the LLM SDK is imported (core never does).
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    def build_request(
        self,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 256,
        model: Optional[str] = None,
        stop: Optional[List[str]] = None,
    ) -> dict:
        """Build the EXACT Anthropic Messages request this adapter would send.

        Prompt caching is MANDATORY on every Anthropic call (claude-api guidance +
        MEGAPLAN §5): the ``system`` block is shipped as a LIST of content blocks
        with ``cache_control: {"type": "ephemeral"}`` on the (single, stable) block,
        so the large per-client persona/classifier system prompt is cached and the
        only variable bytes (the delimited user message) sit AFTER it in the user
        turn. The system string is the cache prefix; the user ``prompt`` is the
        volatile suffix.

        This is a PURE builder — no network, no SDK import — so tests can assert the
        cache_control shape against the request the adapter BUILDS (the C3.4b /
        §6 LLM-seam convention: the prompt-caching assertion runs against the built
        request, never a live round-trip).
        """
        req: dict = {
            "model": model or self._model,
            "max_tokens": max_tokens,
            # cache_control: ephemeral on the system block (MANDATORY).
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": prompt}],
        }
        if stop:
            req["stop_sequences"] = stop
        return req

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
        The request is built by :meth:`build_request` (cache_control: ephemeral on
        the system block — prompt caching is mandatory); the full drafter wiring
        (thread context, register selection) is C3.3.

        **Cost side effect (C3.10).** On a successful call the response's
        ``usage`` is recorded into the configured :class:`CostAccountant` keyed by
        the configured ``client_id`` — per-client attribution, no cross-client
        bleed. The recording is wrapped so a cost-accounting failure can never break
        the reply path, and it does not change the ``Optional[str]`` return value.
        """
        try:
            client = self._ensure_client()
            kwargs = self.build_request(
                system, prompt, max_tokens=max_tokens, model=model, stop=stop
            )
            resp = await client.messages.create(**kwargs)
            # C3.10: record token usage into the per-client accountant (side effect
            # ONLY — the return contract is unchanged). Priced by the request's
            # effective model. Never lets a cost-accounting error break the reply.
            self._record_cost(resp, model=kwargs.get("model"))
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

    def _record_cost(self, resp: object, *, model: Optional[str]) -> None:
        """Record ``resp.usage`` into the per-client accountant (the C3.10 side effect).

        A no-op when no accountant / client_id is wired. Reads ``resp.usage``
        defensively and forwards to :meth:`CostAccountant.record_usage`, keyed by
        ``self._client_id`` so the spend lands in THIS client's bucket and no
        other's. Any failure is swallowed (cost capture must never break a reply).
        """
        if self._accountant is None or self._client_id is None:
            return
        try:
            usage = getattr(resp, "usage", None)
            if usage is None:
                return
            self._accountant.record_usage(
                self._client_id, model=model or self._model, usage=usage
            )
        except Exception:  # pragma: no cover - cost capture must never block a reply
            logger.exception("AnthropicProvider cost recording failed (continuing)")


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
    accountant: Optional[CostAccountant] = None,
    client_id: Optional[int] = None,
) -> LLMProvider:
    """Build a config-selected :class:`LLMProvider` (default: Anthropic).

    ``provider`` is the manifest's ``llm.provider``. Unknown providers raise
    ``ValueError`` so a config typo fails loudly rather than silently disabling
    the LLM. The returned object satisfies the vendored-core ``LLMProvider``
    protocol (runtime-checkable).

    ``accountant`` + ``client_id`` (C3.10) are passed THROUGH to the
    :class:`AnthropicProvider` for per-client cost capture; they are IGNORED for the
    ``null`` provider, which makes no API call and records nothing (cost is $0 on
    the Null / budget-exhausted path).
    """
    key = provider.strip().lower()
    if key not in _PROVIDERS:
        raise ValueError(
            f"unknown LLM provider {provider!r}; expected one of {sorted(_PROVIDERS)}"
        )
    if key == "anthropic":
        return AnthropicProvider(
            api_key=api_key,
            model=model or DEFAULT_MODEL,
            accountant=accountant,
            client_id=client_id,
        )
    return NullLLMProvider()


__all__ = [
    "LLMProvider",
    "AnthropicProvider",
    "NullLLMProvider",
    "build_llm_provider",
    "DEFAULT_MODEL",
    "CostAccountant",
]
