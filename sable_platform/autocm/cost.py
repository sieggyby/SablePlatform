"""Per-client in-process cost accounting for AutoCM LLM spend (MEGAPLAN C3.10).

The C3.10 scope line names "cost tracking into ``cost_events``"; the persisted
``cost_events`` ledger leg (an ``autocm.draft`` / ``autocm.classify`` call-type
row written through :func:`sable_platform.db.cost.log_cost`, attributed to the
client's ``org_id``) is the production sink and is DEFERRED here — see the
DEFERRAL note below. What C3.10 lands NOW, migration-free, is the in-process
**:class:`CostAccountant`**: a per-client token-usage + spend tally that the
:class:`~sable_platform.autocm.llm.AnthropicProvider` records into as a SIDE
EFFECT after each Anthropic API call, keyed by ``client_id``.

Why an in-process accountant and not a new table:

  * **No migration.** This branch is FROZEN at migration head 058 (the swarm owns
    059+ on ``sable-relay-c1``; a new AutoCM table here would collide on merge).
    The accountant is a plain in-memory object — zero schema. The DB-persisted
    cost ledger is the deferred follow-up below.
  * **The seam is already there.** The ``AnthropicProvider`` is the ONLY place an
    LLM SDK + a real token-usage object live (``resp.usage``). Recording into the
    accountant there — without touching the ``LLMProvider`` protocol's
    ``Optional[str]`` return type — keeps the cost capture exactly where the
    tokens are known and nowhere else.
  * **Per-client, no bleed.** Each client's spend lands in its OWN bucket keyed by
    ``client_id``; client A's tokens can never be attributed to client B. The
    isolation is the C3.10 hard exit, mirrored here for cost.

Price comes from the per-model rate table (:data:`PRICE_PER_MTOK`, the same shape
``sable_platform.checkin.synthesize`` uses) so a recorded usage is converted to a
USD figure deterministically. The :class:`~sable_platform.autocm.llm.NullLLMProvider`
makes NO API call and therefore records NOTHING — a Null/budget-exhausted path has
zero cost, which is exactly the R-4 "LLM is garnish" invariant.

DEFERRAL (post-merge migration follow-up, recorded so the swarm 059+ do not
collide). The DB-persisted cost ledger — writing each recorded usage as a
``cost_events`` row (``call_type='autocm.draft'`` / ``'autocm.classify'``,
``org_id`` = the client's org, ``input_tokens`` / ``output_tokens`` / ``cost_usd``
populated; budget enforced via :func:`sable_platform.db.cost.check_budget`) — is a
DEFERRED post-merge change. ``cost_events`` ALREADY EXISTS (it is not a new
table), so persistence is a code wiring + a tiny migration only for a per-call
AutoCM ``call_type`` literal/index IF one is wanted; it is held back ONLY to keep
this branch at migration head 058 and avoid the 059+ collision. Until then the
in-process accountant is the cost-attribution surface for the online handler, and
the existing ``kb.store`` embed-spend path is the one autocm leg that already
writes ``cost_events`` (``autocm.embed``). When the freeze lifts, route
:meth:`CostAccountant.flush_to_cost_events`-style writes through ``log_cost`` per
client and enforce ``check_budget`` before the call.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional

# ---------------------------------------------------------------------------
# Per-model price table (USD per MILLION tokens). Same shape as
# checkin/synthesize.py's _PRICE_PER_MTOK so the two rate tables read alike.
# Cache reads are 1/10 of input price; cache writes are 1.25x input price.
# ---------------------------------------------------------------------------
PRICE_PER_MTOK: Dict[str, Dict[str, float]] = {
    # the AutoCM v1 default drafter/classifier model (llm.DEFAULT_MODEL).
    "claude-opus-4-8": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-opus-4-7": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_write": 1.25},
}

#: the model whose rates are used when a recorded model is not in the table — the
#: AutoCM default (never silently $0: an unknown model is priced at the default
#: rate so the spend tally is conservative, not zero).
DEFAULT_PRICE_MODEL = "claude-opus-4-8"


def price_for_usage(
    model: Optional[str],
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """USD cost for one usage record, priced from :data:`PRICE_PER_MTOK`.

    An unknown / ``None`` model falls back to the :data:`DEFAULT_PRICE_MODEL`
    rates (never $0 — an unrecognized model is priced, conservatively, at the
    default rate). Rounded to 6 decimals, matching ``checkin/synthesize._compute_cost_usd``.
    """
    rates = PRICE_PER_MTOK.get(model or "", PRICE_PER_MTOK[DEFAULT_PRICE_MODEL])
    return round(
        (input_tokens / 1_000_000) * rates["input"]
        + (output_tokens / 1_000_000) * rates["output"]
        + (cache_read_tokens / 1_000_000) * rates["cache_read"]
        + (cache_write_tokens / 1_000_000) * rates["cache_write"],
        6,
    )


@dataclass
class ClientCost:
    """The running per-client spend tally (one bucket in the accountant)."""

    client_id: int
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

    def as_dict(self) -> Dict[str, float]:
        return {
            "client_id": self.client_id,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


class CostAccountant:
    """In-process, per-client token-usage + spend tally (C3.10, migration-free).

    The :class:`~sable_platform.autocm.llm.AnthropicProvider` records into this as
    a SIDE EFFECT after every successful API call, keyed by ``client_id`` — the
    only place an LLM SDK + a real ``usage`` object live. Each client's spend lands
    in its OWN :class:`ClientCost` bucket: client A's tokens can NEVER be
    attributed to client B (the per-client cost-isolation invariant, the cost-side
    mirror of the C3.10 KB/persona isolation exit).

    The accountant is deliberately tiny and dependency-free (no DB, no engine, no
    network) — it is an online-handler scratchpad, not a durable ledger. The
    DB-persisted ``cost_events`` ledger is the deferred post-merge follow-up
    documented at the module top (held back only to keep this branch at migration
    head 058). A process is single-replica per the §2/§8 single-process-state
    caveat, so a plain in-memory dict is the correct shape here; a lock makes the
    accumulation safe even if a future handler records from a worker thread.
    """

    def __init__(self) -> None:
        self._by_client: Dict[int, ClientCost] = {}
        self._lock = threading.Lock()

    # -- recording ----------------------------------------------------------
    def record(
        self,
        client_id: int,
        *,
        model: Optional[str],
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """Record one API call's token usage for ``client_id``; return its USD cost.

        Computes the call's cost from :func:`price_for_usage` and adds the tokens +
        cost to ``client_id``'s bucket ONLY — no other client's tally is touched.
        Returns the cost of THIS call (the running per-client total is read via
        :meth:`cost_for`). Negative token counts are clamped to 0 (a defensive
        SDK-usage guard) so a malformed usage object can never decrement a tally.
        """
        i = max(0, int(input_tokens or 0))
        o = max(0, int(output_tokens or 0))
        cr = max(0, int(cache_read_tokens or 0))
        cw = max(0, int(cache_write_tokens or 0))
        call_cost = price_for_usage(
            model,
            input_tokens=i,
            output_tokens=o,
            cache_read_tokens=cr,
            cache_write_tokens=cw,
        )
        with self._lock:
            bucket = self._by_client.get(client_id)
            if bucket is None:
                bucket = ClientCost(client_id=client_id)
                self._by_client[client_id] = bucket
            bucket.calls += 1
            bucket.input_tokens += i
            bucket.output_tokens += o
            bucket.cache_read_tokens += cr
            bucket.cache_write_tokens += cw
            bucket.cost_usd += call_cost
        return call_cost

    def record_usage(
        self, client_id: int, *, model: Optional[str], usage: object
    ) -> float:
        """Record from a raw Anthropic ``usage`` object (the SDK-shaped side effect).

        Reads ``input_tokens`` / ``output_tokens`` / ``cache_read_input_tokens`` /
        ``cache_creation_input_tokens`` off ``usage`` defensively (any missing
        attribute is treated as 0) and forwards to :meth:`record`. This is the exact
        call the :class:`AnthropicProvider` makes after ``messages.create`` — it
        never raises on a partial / unexpected usage shape (cost capture must never
        break the reply path).
        """
        return self.record(
            client_id,
            model=model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )

    # -- reads --------------------------------------------------------------
    def cost_for(self, client_id: int) -> float:
        """Running USD spend attributed to ``client_id`` (0.0 if never recorded)."""
        with self._lock:
            bucket = self._by_client.get(client_id)
            return round(bucket.cost_usd, 6) if bucket is not None else 0.0

    def tokens_for(self, client_id: int) -> int:
        """Total (input+output) tokens attributed to ``client_id`` (0 if none)."""
        with self._lock:
            bucket = self._by_client.get(client_id)
            if bucket is None:
                return 0
            return bucket.input_tokens + bucket.output_tokens

    def calls_for(self, client_id: int) -> int:
        """Number of recorded API calls for ``client_id`` (0 if none)."""
        with self._lock:
            bucket = self._by_client.get(client_id)
            return bucket.calls if bucket is not None else 0

    def snapshot(self, client_id: int) -> Optional[ClientCost]:
        """A copy of ``client_id``'s bucket (or ``None`` if it never recorded)."""
        with self._lock:
            bucket = self._by_client.get(client_id)
            if bucket is None:
                return None
            return ClientCost(**bucket.as_dict())  # type: ignore[arg-type]

    def clients(self) -> List[int]:
        """Client ids that have any recorded spend (sorted, deterministic)."""
        with self._lock:
            return sorted(self._by_client)

    def total_cost(self) -> float:
        """Sum of USD spend across ALL clients (the process-wide tally)."""
        with self._lock:
            return round(sum(b.cost_usd for b in self._by_client.values()), 6)

    def as_dict(self) -> Mapping[int, Dict[str, float]]:
        """Per-client tally as a plain dict (audit / debug / digest view)."""
        with self._lock:
            return {cid: b.as_dict() for cid, b in self._by_client.items()}

    def reset(self, client_id: Optional[int] = None) -> None:
        """Clear one client's tally (or the whole accountant when ``client_id`` is None)."""
        with self._lock:
            if client_id is None:
                self._by_client.clear()
            else:
                self._by_client.pop(client_id, None)


__all__ = [
    "PRICE_PER_MTOK",
    "DEFAULT_PRICE_MODEL",
    "price_for_usage",
    "ClientCost",
    "CostAccountant",
]
