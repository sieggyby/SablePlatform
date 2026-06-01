"""C3.2b — kb.onchain tests.

Exit-criterion coverage (MEGAPLAN C3.2b tests + exit/audit):
  * onchain adapter is per-client RPC-safe — TWO clients with DISTINCT RPC keys,
    asserted NO key bleed across the onchain adapter (the security property: each
    client's calls use only that client's key/endpoint);
  * named queries map through the bound transport; 60s per-query cache;
    degrade-on-failure (KB_DESIGN §4); secret-reference resolution rejects inline
    literals (PRODUCTIZATION §5).

All offline — FakeRpcTransport records the endpoint each call used; NO real network.
"""
from __future__ import annotations

import pytest

from sable_platform.autocm.kb.onchain import (
    AlchemyOnchainAdapter,
    FakeRpcTransport,
    OnchainAdapterRegistry,
    OnchainQuery,
    SecretResolutionError,
    resolve_rpc_endpoint,
)


@pytest.fixture(autouse=True)
def _reset_seen():
    FakeRpcTransport.reset()
    yield
    FakeRpcTransport.reset()


def _fake_factory():
    """A transport factory that builds a FakeRpcTransport bound to the endpoint."""
    return lambda endpoint: FakeRpcTransport(endpoint)


# ---------------------------------------------------------------------------
# secret-reference resolution (PRODUCTIZATION §5 — never an inline literal)
# ---------------------------------------------------------------------------
def test_resolve_endpoint_fills_key_template_from_env(monkeypatch) -> None:
    monkeypatch.setenv("RM_ALCHEMY_KEY", "rm-secret-123")
    url = resolve_rpc_endpoint(
        {"endpoint": "https://base-mainnet.g.alchemy.com/v2/{key}",
         "api_key_ref": "env:RM_ALCHEMY_KEY"}
    )
    assert url == "https://base-mainnet.g.alchemy.com/v2/rm-secret-123"


def test_resolve_endpoint_appends_key_when_no_template(monkeypatch) -> None:
    monkeypatch.setenv("RM_ALCHEMY_KEY", "abc")
    url = resolve_rpc_endpoint(
        {"endpoint": "https://base-mainnet.g.alchemy.com/v2", "api_key_ref": "env:RM_ALCHEMY_KEY"}
    )
    assert url == "https://base-mainnet.g.alchemy.com/v2/abc"


def test_resolve_endpoint_whole_url_ref(monkeypatch) -> None:
    monkeypatch.setenv("RM_BASE_RPC_URL", "https://rpc.example/v2/key")
    assert resolve_rpc_endpoint({"endpoint_ref": "env:RM_BASE_RPC_URL"}) == "https://rpc.example/v2/key"


def test_resolve_endpoint_rejects_inline_literal_key() -> None:
    # an inline literal in a *_ref field is the secrets-in-config mistake — rejected
    with pytest.raises(SecretResolutionError):
        resolve_rpc_endpoint(
            {"endpoint": "https://x/{key}", "api_key_ref": "raw-inline-token"}
        )


def test_resolve_endpoint_missing_env_raises(monkeypatch) -> None:
    monkeypatch.delenv("RM_NOT_SET", raising=False)
    with pytest.raises(SecretResolutionError):
        resolve_rpc_endpoint({"endpoint": "https://x/{key}", "api_key_ref": "env:RM_NOT_SET"})


# ---------------------------------------------------------------------------
# THE C3.2b SECURITY PROPERTY: two clients, distinct keys, NO key bleed
# ---------------------------------------------------------------------------
def test_two_clients_distinct_keys_no_bleed(monkeypatch) -> None:
    monkeypatch.setenv("CLIENT_A_KEY", "key-AAA")
    monkeypatch.setenv("CLIENT_B_KEY", "key-BBB")
    manifests = {
        1: {
            "endpoint": "https://base.g.alchemy.com/v2/{key}",
            "api_key_ref": "env:CLIENT_A_KEY",
            "queries": {"vault_tvl": {"method": "eth_call", "params": ["A"]}},
        },
        2: {
            "endpoint": "https://base.g.alchemy.com/v2/{key}",
            "api_key_ref": "env:CLIENT_B_KEY",
            "queries": {"vault_tvl": {"method": "eth_call", "params": ["B"]}},
        },
    }
    registry = OnchainAdapterRegistry(manifests, transport_factory=_fake_factory())

    # each client's adapter is bound to ITS OWN key-bearing endpoint
    a = registry.adapter_for(1)
    b = registry.adapter_for(2)
    assert a.endpoint == "https://base.g.alchemy.com/v2/key-AAA"
    assert b.endpoint == "https://base.g.alchemy.com/v2/key-BBB"
    assert a.endpoint != b.endpoint  # no shared endpoint

    registry.query(1, "vault_tvl")
    registry.query(2, "vault_tvl")

    # the recorded endpoints prove client A only ever hit A's key-endpoint and
    # client B only ever hit B's — no key bleed across the onchain adapter.
    seen = FakeRpcTransport.seen_endpoints
    assert "https://base.g.alchemy.com/v2/key-AAA" in seen
    assert "https://base.g.alchemy.com/v2/key-BBB" in seen
    # client A's key NEVER appears in any of client B's adapter calls and vice-versa
    a_calls = a._transport.calls  # type: ignore[attr-defined]
    b_calls = b._transport.calls  # type: ignore[attr-defined]
    assert all(c["endpoint"] == a.endpoint for c in a_calls)
    assert all(c["endpoint"] == b.endpoint for c in b_calls)
    assert "key-BBB" not in a.endpoint
    assert "key-AAA" not in b.endpoint


def test_adapter_refuses_foreign_client_id(monkeypatch) -> None:
    monkeypatch.setenv("CLIENT_A_KEY", "key-AAA")
    registry = OnchainAdapterRegistry(
        {1: {"endpoint": "https://x/{key}", "api_key_ref": "env:CLIENT_A_KEY",
             "queries": {"vault_tvl": {}}}},
        transport_factory=_fake_factory(),
    )
    adapter = registry.adapter_for(1)
    # an adapter bound to client 1 will not serve a query labeled for client 2
    with pytest.raises(ValueError):
        adapter.query(2, "vault_tvl")


def test_registry_unconfigured_client_raises() -> None:
    registry = OnchainAdapterRegistry({}, transport_factory=_fake_factory())
    with pytest.raises(KeyError):
        registry.adapter_for(42)


# ---------------------------------------------------------------------------
# named queries + behavior
# ---------------------------------------------------------------------------
def _adapter(client_id=1, results=None, queries=None, clock=None):
    transport = FakeRpcTransport("https://endpoint/key-X", results=results)
    q = queries or {"vault_tvl": OnchainQuery("vault_tvl", "eth_call", ["0xabc"])}
    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    return AlchemyOnchainAdapter(client_id=client_id, transport=transport, queries=q, **kwargs)


def test_query_maps_to_method_and_params() -> None:
    adapter = _adapter()
    out = adapter.query(1, "vault_tvl")
    assert out["available"] is True
    assert out["client_id"] == 1
    # FakeRpcTransport echoes method/params so we can assert the mapping
    assert out["result"]["method"] == "eth_call"
    assert out["result"]["params"] == ["0xabc"]


def test_supported_queries_lists_registered() -> None:
    adapter = _adapter(queries={
        "vault_tvl": OnchainQuery("vault_tvl", "eth_call"),
        "last_buyback": OnchainQuery("last_buyback", "eth_call"),
    })
    assert adapter.supported_queries() == ["last_buyback", "vault_tvl"]


def test_unknown_query_degrades_not_raises() -> None:
    out = _adapter().query(1, "does_not_exist")
    assert out["available"] is False
    assert out["error"] == "unknown_query"


def test_transport_failure_degrades(monkeypatch) -> None:
    class BoomTransport:
        endpoint = "https://endpoint/key-X"

        def call(self, method, params):
            raise RuntimeError("rpc down")

    adapter = AlchemyOnchainAdapter(
        client_id=1, transport=BoomTransport(),
        queries={"vault_tvl": OnchainQuery("vault_tvl", "eth_call")},
    )
    out = adapter.query(1, "vault_tvl")
    # KB_DESIGN §4: degrade to unavailable, never raise
    assert out["available"] is False
    assert out["error"] == "rpc_unavailable"


# ---------------------------------------------------------------------------
# 60s per-query cache (KB_DESIGN §4)
# ---------------------------------------------------------------------------
def test_query_result_cached_within_ttl() -> None:
    fake_time = {"t": 1000.0}
    adapter = _adapter(clock=lambda: fake_time["t"])
    first = adapter.query(1, "vault_tvl")
    assert first["cached"] is False
    # within 60s → served from cache, no new transport call
    fake_time["t"] = 1000.0 + 30.0
    second = adapter.query(1, "vault_tvl")
    assert second["cached"] is True
    assert second["result"] == first["result"]
    # only ONE transport call so far (the cache hit didn't hit the wire)
    assert len(adapter._transport.calls) == 1  # type: ignore[attr-defined]


def test_query_cache_expires_after_ttl() -> None:
    fake_time = {"t": 1000.0}
    adapter = _adapter(clock=lambda: fake_time["t"])
    adapter.query(1, "vault_tvl")
    # past 60s → cache expired, new transport call
    fake_time["t"] = 1000.0 + 61.0
    out = adapter.query(1, "vault_tvl")
    assert out["cached"] is False
    assert len(adapter._transport.calls) == 2  # type: ignore[attr-defined]
