"""KB on-chain source (C3.2b) — per-client chain-RPC adapter (Alchemy-style).

``KB_DESIGN §4`` on-chain integration. Per-client adapter pattern over a Base
(or any EVM) JSON-RPC endpoint:

  * Pre-defined named queries (``vault_tvl``, ``last_buyback``, ``total_deployed``,
    ``agent_action_log``, ``contract_holders_count``) — the per-client query
    registry comes from the deployment manifest's ``onchain`` block.
  * Results cached **60s per (client, query)** to avoid RPC spam under load.
  * Failures **degrade** to ``{"available": False, ...}`` rather than raising — a
    dead RPC must never crash the reply path (``KB_DESIGN §4``: "degrade to 'live
    data temporarily unavailable' rather than escalating every query").

SECURITY PROPERTY (the C3.2b gate — RPC key isolation):
    Each client's on-chain calls use ONLY that client's RPC key/URL. A client's
    key is resolved from THAT client's manifest (an env-var NAME or secret-store
    handle — never an inline literal, per ``PRODUCTIZATION §5``). The
    :class:`OnchainAdapterRegistry` builds one adapter per client, each bound to a
    transport carrying only that client's resolved endpoint, so there is no shared
    mutable key state two clients could read across — verified by
    ``tests/autocm/test_kb_onchain.py`` (two clients, distinct keys, no bleed).

NETWORK ISOLATION FOR TESTS: all RPC goes through the :class:`RpcTransport` seam.
Production uses :class:`HttpRpcTransport` (sync ``httpx``); tests inject a
:class:`FakeRpcTransport` that records the endpoint each call used — so the
key-isolation property is directly assertable WITHOUT real network.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)

# 60s per-query cache (KB_DESIGN §4 "results cached for 60s per query").
CACHE_TTL_SECONDS = 60.0

# The pre-defined query names RobotMoney's v1 manifest exposes (KB_DESIGN §4). A
# per-client adapter may expose a SUBSET (only the queries that client configured).
DEFAULT_QUERY_NAMES = (
    "vault_tvl",
    "last_buyback",
    "total_deployed",
    "agent_action_log",
    "contract_holders_count",
)


# ---------------------------------------------------------------------------
# RPC endpoint resolution (per-client; secret-store handle / env NAME, never inline)
# ---------------------------------------------------------------------------
class SecretResolutionError(RuntimeError):
    """Raised when a client's RPC key reference cannot be resolved."""


def resolve_rpc_endpoint(rpc_config: Dict[str, Any]) -> str:
    """Resolve a client's RPC endpoint URL from its manifest ``onchain`` block.

    The manifest carries an endpoint TEMPLATE + a key REFERENCE, never an inline
    key (``PRODUCTIZATION §5`` "no secrets in config rows"). Two supported shapes:

      * ``{"endpoint": "https://base-mainnet.g.alchemy.com/v2/{key}",
           "api_key_ref": "env:RM_ALCHEMY_KEY"}``  → ``{key}`` is filled from the
        env var named ``RM_ALCHEMY_KEY``.
      * ``{"endpoint_ref": "env:RM_BASE_RPC_URL"}``  → the WHOLE URL is the env var.

    A ``*_ref`` value MUST be an ``env:NAME`` (or ``secret://…``) handle. An inline
    literal URL/key in a ``*_ref`` field is rejected — the loader catches the
    secrets-in-config mistake here, same posture as the C3.1 manifest validator.
    """
    endpoint_ref = rpc_config.get("endpoint_ref")
    if endpoint_ref is not None:
        return _resolve_ref(endpoint_ref)

    endpoint = rpc_config.get("endpoint")
    if not endpoint:
        raise SecretResolutionError("onchain config has neither endpoint nor endpoint_ref")
    key_ref = rpc_config.get("api_key_ref")
    if key_ref is None:
        # endpoint already complete (e.g. a public RPC with no key)
        return endpoint
    key = _resolve_ref(key_ref)
    if "{key}" in endpoint:
        return endpoint.replace("{key}", key)
    # no template slot — append as the path segment (Alchemy v2 style)
    return endpoint.rstrip("/") + "/" + key


def _resolve_ref(ref: str) -> str:
    """Resolve an ``env:NAME`` / ``secret://NAME`` handle to its value.

    A bare literal (no recognized scheme) is REJECTED — a builder must reference a
    secret, not inline it. ``secret://`` is treated as an env lookup in v1 (the
    real secret store is a deployment concern; the contract is the same: a NAME).
    """
    if not isinstance(ref, str):
        raise SecretResolutionError(f"secret reference must be a string, got {type(ref)!r}")
    if ref.startswith("env:"):
        name = ref[len("env:"):]
    elif ref.startswith("secret://"):
        name = ref[len("secret://"):]
    else:
        raise SecretResolutionError(
            f"RPC key/endpoint reference {ref!r} must be an env:/secret:// handle, "
            f"never an inline literal (PRODUCTIZATION §5)"
        )
    value = os.environ.get(name)
    if not value:
        raise SecretResolutionError(f"env var {name!r} (from {ref!r}) is unset/empty")
    return value


# ---------------------------------------------------------------------------
# RPC transport seam (injectable; FakeRpcTransport for tests — NO real network)
# ---------------------------------------------------------------------------
class RpcTransport(Protocol):
    """A bound JSON-RPC transport: ONE endpoint, ``call(method, params) -> result``.

    A transport is bound to exactly one client's resolved endpoint at construction;
    it never accepts an endpoint per call, so a client's transport cannot be made
    to talk to another client's endpoint.
    """

    @property
    def endpoint(self) -> str:
        ...

    def call(self, method: str, params: List[Any]) -> Any:
        """Run a JSON-RPC method; return ``result`` or raise on transport failure."""
        ...


class HttpRpcTransport:
    """Production :class:`RpcTransport` — sync ``httpx`` JSON-RPC POST to ONE endpoint.

    The endpoint (already key-bearing, resolved per-client) is fixed at
    construction. Lazy-imports httpx. Raises on non-200 / JSON-RPC error so the
    adapter's degrade-on-failure layer can catch it.
    """

    def __init__(self, endpoint: str, *, timeout: float = 15.0) -> None:
        self._endpoint = endpoint
        self._timeout = timeout

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def call(self, method: str, params: List[Any]) -> Any:  # pragma: no cover - network
        import httpx

        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(self._endpoint, json=payload)
        resp.raise_for_status()
        body = resp.json()
        if "error" in body and body["error"]:
            raise RuntimeError(f"JSON-RPC error: {body['error']}")
        return body.get("result")


class FakeRpcTransport:
    """Deterministic offline :class:`RpcTransport` for tests — bound to ONE endpoint.

    Records every endpoint it was asked to call on the SHARED class-level
    :data:`seen_endpoints` log (so a test can assert which endpoints were hit
    across all adapters) AND on its own ``calls`` list. Returns a canned result per
    method (or a default echo). NEVER touches the network.

    Because each instance is bound to one endpoint and the registry builds one
    transport per client, the recorded endpoints prove no key bleed.
    """

    # class-level call log across all instances — lets the key-isolation test see
    # the full set of endpoints used by all clients in one place.
    seen_endpoints: List[str] = []

    def __init__(self, endpoint: str, results: Optional[Dict[str, Any]] = None) -> None:
        self._endpoint = endpoint
        self._results = dict(results or {})
        self.calls: List[Dict[str, Any]] = []

    @classmethod
    def reset(cls) -> None:
        cls.seen_endpoints = []

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def call(self, method: str, params: List[Any]) -> Any:
        self.calls.append({"endpoint": self._endpoint, "method": method, "params": params})
        FakeRpcTransport.seen_endpoints.append(self._endpoint)
        if method in self._results:
            return self._results[method]
        # deterministic default echo so query mapping is testable without canning
        # every method.
        return {"method": method, "params": params, "endpoint": self._endpoint}


# A transport factory: endpoint -> RpcTransport. Production default builds an
# HttpRpcTransport; tests pass a factory that builds FakeRpcTransport(endpoint).
TransportFactory = Callable[[str], RpcTransport]


def _default_transport_factory(endpoint: str) -> RpcTransport:
    return HttpRpcTransport(endpoint)


# ---------------------------------------------------------------------------
# Named on-chain queries → (method, params) — the manifest-configured registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OnchainQuery:
    """A named on-chain query bound to a JSON-RPC method + params (KB_DESIGN §4).

    ``vault_tvl`` etc. map to a concrete ``eth_call`` (or ``eth_getBalance`` …) on a
    specific contract. v1 carries the method/params verbatim from the manifest so
    the adapter stays contract-agnostic; ABI-encoding niceties are a deployment
    concern layered on top of this contract.
    """

    name: str
    method: str
    params: List[Any] = field(default_factory=list)


def _queries_from_config(query_config: Dict[str, Any]) -> Dict[str, OnchainQuery]:
    """Build the per-client named-query registry from the manifest ``queries`` block.

    ``query_config`` shape: ``{"vault_tvl": {"method": "eth_call", "params": [...]},
    ...}``. A query with no explicit method defaults to ``eth_call``.
    """
    out: Dict[str, OnchainQuery] = {}
    for name, spec in (query_config or {}).items():
        spec = spec or {}
        out[name] = OnchainQuery(
            name=name,
            method=spec.get("method", "eth_call"),
            params=list(spec.get("params", [])),
        )
    return out


# ---------------------------------------------------------------------------
# Per-client adapter + registry
# ---------------------------------------------------------------------------
class OnchainAdapter(Protocol):
    """Per-client chain RPC adapter — each client's calls use only that client's key."""

    def query(self, client_id: int, query_name: str) -> dict:
        ...

    def supported_queries(self) -> List[str]:
        ...


class AlchemyOnchainAdapter:
    """Per-client Alchemy-style on-chain adapter (KB_DESIGN §4).

    Bound to ONE client at construction: it holds that client's :class:`RpcTransport`
    (carrying only that client's resolved endpoint) and that client's named-query
    registry. ``query`` runs a named query through THIS client's transport, with a
    60s per-query cache and degrade-on-failure. The ``client_id`` argument is
    asserted against the bound client so a caller can never run client A's query
    object against client B's id.
    """

    def __init__(
        self,
        *,
        client_id: int,
        transport: RpcTransport,
        queries: Dict[str, OnchainQuery],
        cache_ttl: float = CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_id = client_id
        self._transport = transport
        self._queries = dict(queries)
        self._cache_ttl = cache_ttl
        self._clock = clock
        self._cache: Dict[str, tuple] = {}  # query_name -> (expires_at, result)

    @property
    def client_id(self) -> int:
        return self._client_id

    @property
    def endpoint(self) -> str:
        """The resolved endpoint THIS adapter's transport is bound to."""
        return self._transport.endpoint

    def supported_queries(self) -> List[str]:
        return sorted(self._queries)

    def query(self, client_id: int, query_name: str) -> dict:
        """Run a named on-chain query for ``client_id`` (must be the bound client).

        Returns ``{"available": True, "query": name, "result": <rpc result>,
        "cached": bool, "client_id": id}`` on success, or
        ``{"available": False, "query": name, "error": <reason>, "client_id": id}``
        on an unknown query or a transport failure (degrade, never raise —
        KB_DESIGN §4).
        """
        if client_id != self._client_id:
            # Hard isolation guard: an adapter bound to client A refuses to serve a
            # query labeled for client B. This makes accidental cross-client use a
            # loud failure, not a silent key-bleed.
            raise ValueError(
                f"adapter bound to client {self._client_id} cannot serve "
                f"client {client_id}"
            )
        q = self._queries.get(query_name)
        if q is None:
            return {
                "available": False,
                "query": query_name,
                "error": "unknown_query",
                "client_id": self._client_id,
            }
        now = self._clock()
        cached = self._cache.get(query_name)
        if cached is not None and cached[0] > now:
            return {
                "available": True,
                "query": query_name,
                "result": cached[1],
                "cached": True,
                "client_id": self._client_id,
            }
        try:
            result = self._transport.call(q.method, q.params)
        except Exception as exc:  # degrade, never raise (KB_DESIGN §4)
            logger.warning(
                "onchain query %s for client %s failed: %s",
                query_name,
                self._client_id,
                exc,
            )
            return {
                "available": False,
                "query": query_name,
                "error": "rpc_unavailable",
                "client_id": self._client_id,
            }
        self._cache[query_name] = (now + self._cache_ttl, result)
        return {
            "available": True,
            "query": query_name,
            "result": result,
            "cached": False,
            "client_id": self._client_id,
        }


class OnchainAdapterRegistry:
    """Builds + caches ONE :class:`AlchemyOnchainAdapter` per client.

    The registry is the multi-tenant boundary: each client's adapter is built from
    THAT client's manifest ``onchain`` block (its own endpoint reference + query
    set), so two clients never share an endpoint or a key. The transport factory is
    injectable (tests pass one that builds :class:`FakeRpcTransport`). Adapters are
    memoized per ``client_id`` so the 60s cache is shared across calls for a client
    within the registry's lifetime.
    """

    def __init__(
        self,
        manifests: Dict[int, Dict[str, Any]],
        *,
        transport_factory: TransportFactory = _default_transport_factory,
        cache_ttl: float = CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # manifests: client_id -> that client's `onchain` manifest block, e.g.
        #   {"endpoint": "...{key}", "api_key_ref": "env:RM_ALCHEMY_KEY",
        #    "queries": {"vault_tvl": {"method": "eth_call", "params": [...]}}}
        self._manifests = dict(manifests)
        self._transport_factory = transport_factory
        self._cache_ttl = cache_ttl
        self._clock = clock
        self._adapters: Dict[int, AlchemyOnchainAdapter] = {}

    def adapter_for(self, client_id: int) -> AlchemyOnchainAdapter:
        """Return (building + memoizing) the adapter for one client.

        Each client's endpoint is resolved from ITS OWN manifest block — the key
        reference never crosses clients. Raises ``KeyError`` for an unconfigured
        client and ``SecretResolutionError`` if its key reference can't resolve.
        """
        if client_id in self._adapters:
            return self._adapters[client_id]
        manifest = self._manifests.get(client_id)
        if manifest is None:
            raise KeyError(f"no onchain manifest for client {client_id}")
        endpoint = resolve_rpc_endpoint(manifest)
        transport = self._transport_factory(endpoint)
        queries = _queries_from_config(manifest.get("queries", {}))
        adapter = AlchemyOnchainAdapter(
            client_id=client_id,
            transport=transport,
            queries=queries,
            cache_ttl=self._cache_ttl,
            clock=self._clock,
        )
        self._adapters[client_id] = adapter
        return adapter

    def query(self, client_id: int, query_name: str) -> dict:
        """Convenience: resolve the client's adapter and run a named query."""
        return self.adapter_for(client_id).query(client_id, query_name)


class NotImplementedOnchainAdapter:
    """Stub adapter retained for callers not yet wired to a real adapter.

    Raises so accidental hot-path use is loud. The real path is
    :class:`AlchemyOnchainAdapter` built per-client by :class:`OnchainAdapterRegistry`.
    """

    def query(self, client_id: int, query_name: str) -> dict:
        raise NotImplementedError("use OnchainAdapterRegistry / AlchemyOnchainAdapter (C3.2b)")

    def supported_queries(self) -> List[str]:
        return []


__all__ = [
    # adapter + registry
    "OnchainAdapter",
    "AlchemyOnchainAdapter",
    "OnchainAdapterRegistry",
    "NotImplementedOnchainAdapter",
    # query registry
    "OnchainQuery",
    "DEFAULT_QUERY_NAMES",
    # transport seam
    "RpcTransport",
    "HttpRpcTransport",
    "FakeRpcTransport",
    "TransportFactory",
    # endpoint / secret resolution
    "resolve_rpc_endpoint",
    "SecretResolutionError",
    "CACHE_TTL_SECONDS",
]
