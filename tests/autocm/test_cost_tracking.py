"""C3.10 — per-client cost tracking (in-process CostAccountant, no migration).

The C3.10 cost-tracking leg: the :class:`AnthropicProvider` records each call's
token usage into a per-client :class:`CostAccountant` as a SIDE EFFECT, keyed by
``client_id``, WITHOUT changing the ``LLMProvider`` protocol's ``Optional[str]``
return type. The :class:`NullLLMProvider` records NOTHING.

Asserted here:
  * costs are attributed PER-CLIENT (A's spend == sum of A's calls' costs);
  * NO cost bleed — A's cost never lands on B (and vice-versa);
  * the Null provider records nothing (the $0 / budget-exhausted path);
  * the side effect does not change the returned completion (Optional[str]);
  * price comes from the model rate table (``PRICE_PER_MTOK``);
  * a missing / malformed usage object never crashes the reply path.

NO real Anthropic / network: a FAKE anthropic client (records usage, returns text)
is injected into the adapter's lazily-built ``_client`` slot — the real SDK is
never imported.
"""
from __future__ import annotations

import asyncio

import pytest

from sable_platform.autocm.cost import (
    DEFAULT_PRICE_MODEL,
    PRICE_PER_MTOK,
    CostAccountant,
    price_for_usage,
)
from sable_platform.autocm.llm import (
    DEFAULT_MODEL,
    AnthropicProvider,
    NullLLMProvider,
    build_llm_provider,
)


# ---------------------------------------------------------------------------
# A FAKE anthropic client — records usage, returns recorded text. NO network.
# ---------------------------------------------------------------------------
class _Usage:
    def __init__(self, *, input_tokens, output_tokens, cache_read=0, cache_write=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_write


class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text, usage):
        self.content = [_TextBlock(text)]
        self.usage = usage


class _Messages:
    def __init__(self, reply_text, usage, *, no_usage=False):
        self._reply = reply_text
        self._usage = usage
        self._no_usage = no_usage
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        resp = _Resp(self._reply, self._usage)
        if self._no_usage:
            resp.usage = None
        return resp


class _FakeAnthropic:
    def __init__(self, reply_text="ok", *, usage=None, no_usage=False):
        self.messages = _Messages(
            reply_text,
            usage or _Usage(input_tokens=1000, output_tokens=200),
            no_usage=no_usage,
        )


def _provider_with_fake_client(accountant, client_id, *, fake):
    """Build an AnthropicProvider wired to the accountant + a pre-injected fake client."""
    p = AnthropicProvider(accountant=accountant, client_id=client_id)
    p._client = fake  # bypass the lazy real-SDK construction (no anthropic import)
    return p


# ===========================================================================
# Price table — cost comes from the model rate table.
# ===========================================================================
def test_price_for_usage_from_model_table():
    # 1M input tokens of the default model == that model's input rate.
    rate = PRICE_PER_MTOK[DEFAULT_MODEL]["input"]
    assert price_for_usage(DEFAULT_MODEL, input_tokens=1_000_000) == pytest.approx(rate)
    # default model is the AutoCM default drafter/classifier model.
    assert DEFAULT_PRICE_MODEL == DEFAULT_MODEL
    assert DEFAULT_MODEL in PRICE_PER_MTOK
    # an unknown model is priced at the DEFAULT rate (never silently $0).
    assert price_for_usage("totally-unknown-model", input_tokens=1_000_000) == pytest.approx(
        PRICE_PER_MTOK[DEFAULT_PRICE_MODEL]["input"]
    )


# ===========================================================================
# The side effect — a completion records token usage into the accountant.
# ===========================================================================
def test_complete_records_usage_into_accountant_without_changing_return():
    acct = CostAccountant()
    usage = _Usage(input_tokens=1200, output_tokens=300, cache_write=800)
    fake = _FakeAnthropic("hello world", usage=usage)
    p = _provider_with_fake_client(acct, client_id=7, fake=fake)

    out = asyncio.run(p.complete("sys", "prompt"))

    # the return contract is unchanged: the completion text (Optional[str]).
    assert out == "hello world"
    # the usage was recorded for client 7, priced from the model table.
    expected = price_for_usage(
        DEFAULT_MODEL, input_tokens=1200, output_tokens=300, cache_write_tokens=800
    )
    assert acct.cost_for(7) == pytest.approx(expected)
    assert acct.tokens_for(7) == 1500  # input + output
    assert acct.calls_for(7) == 1


# ===========================================================================
# Per-client attribution + NO cost bleed (A cost never lands on B).
# ===========================================================================
def test_costs_are_attributed_per_client_with_no_bleed():
    acct = CostAccountant()  # ONE shared accountant, two per-client providers

    a_usage = _Usage(input_tokens=2000, output_tokens=500)
    b_usage = _Usage(input_tokens=400, output_tokens=100)
    prov_a = _provider_with_fake_client(acct, client_id=1, fake=_FakeAnthropic("A", usage=a_usage))
    prov_b = _provider_with_fake_client(acct, client_id=2, fake=_FakeAnthropic("B", usage=b_usage))

    # client A makes THREE calls; client B makes ONE.
    for _ in range(3):
        assert asyncio.run(prov_a.complete("s", "p")) == "A"
    assert asyncio.run(prov_b.complete("s", "p")) == "B"

    a_per_call = price_for_usage(DEFAULT_MODEL, input_tokens=2000, output_tokens=500)
    b_per_call = price_for_usage(DEFAULT_MODEL, input_tokens=400, output_tokens=100)

    # A's cost is EXACTLY 3 of A's calls; B's is EXACTLY 1 of B's calls.
    assert acct.cost_for(1) == pytest.approx(3 * a_per_call)
    assert acct.cost_for(2) == pytest.approx(b_per_call)
    assert acct.calls_for(1) == 3
    assert acct.calls_for(2) == 1
    assert acct.tokens_for(1) == 3 * 2500
    assert acct.tokens_for(2) == 500

    # NO BLEED: A's spend is not on B and B's is not on A. Distinct per-call costs +
    # distinct call counts make the buckets provably disjoint.
    assert acct.cost_for(1) != acct.cost_for(2)
    assert acct.clients() == [1, 2]
    # the process-wide total is exactly A + B (nothing double-counted or lost).
    assert acct.total_cost() == pytest.approx(3 * a_per_call + b_per_call)


def test_a_cost_never_lands_on_b_when_only_a_runs():
    acct = CostAccountant()
    prov_a = _provider_with_fake_client(
        acct, client_id=1, fake=_FakeAnthropic("A", usage=_Usage(input_tokens=900, output_tokens=90))
    )
    asyncio.run(prov_a.complete("s", "p"))

    assert acct.cost_for(1) > 0.0
    # client 2 never ran → its bucket is empty (no bleed from A).
    assert acct.cost_for(2) == 0.0
    assert acct.tokens_for(2) == 0
    assert acct.calls_for(2) == 0
    assert acct.snapshot(2) is None
    assert 2 not in acct.clients()


# ===========================================================================
# The Null provider records NOTHING ($0 / budget-exhausted path).
# ===========================================================================
def test_null_provider_records_nothing():
    acct = CostAccountant()
    null = NullLLMProvider()
    # the Null provider's complete returns None and never touches the accountant.
    assert asyncio.run(null.complete("s", "p")) is None
    assert acct.clients() == []
    assert acct.total_cost() == 0.0

    # build_llm_provider('null') ignores the accountant/client_id and records nothing.
    null2 = build_llm_provider("null", accountant=acct, client_id=5)
    assert isinstance(null2, NullLLMProvider)
    assert asyncio.run(null2.complete("s", "p")) is None
    assert acct.cost_for(5) == 0.0
    assert acct.clients() == []


def test_anthropic_provider_without_accountant_is_a_noop_capture():
    # an adapter built with no accountant still works and simply captures nothing.
    p = _provider_with_fake_client(None, None, fake=_FakeAnthropic("hi"))
    assert asyncio.run(p.complete("s", "p")) == "hi"  # return unaffected


# ===========================================================================
# Robustness — a missing / malformed usage never crashes the reply path.
# ===========================================================================
def test_missing_usage_does_not_crash_or_record():
    acct = CostAccountant()
    fake = _FakeAnthropic("still replies", no_usage=True)
    p = _provider_with_fake_client(acct, client_id=3, fake=fake)

    # the reply still comes back; nothing was recorded (usage was None).
    assert asyncio.run(p.complete("s", "p")) == "still replies"
    assert acct.cost_for(3) == 0.0
    assert acct.calls_for(3) == 0


def test_record_clamps_negative_tokens():
    acct = CostAccountant()
    # a malformed usage with negative counts must never decrement a tally.
    cost = acct.record(9, model=DEFAULT_MODEL, input_tokens=-100, output_tokens=-5)
    assert cost == 0.0
    assert acct.tokens_for(9) == 0


# ===========================================================================
# build_llm_provider threads the accountant through to the Anthropic adapter.
# ===========================================================================
def test_build_llm_provider_threads_accountant_to_anthropic():
    acct = CostAccountant()
    prov = build_llm_provider("anthropic", accountant=acct, client_id=11)
    assert isinstance(prov, AnthropicProvider)
    # the adapter carries the per-client cost wiring.
    assert prov._accountant is acct
    assert prov._client_id == 11
    # drive a call through a fake client → cost lands on client 11 only.
    prov._client = _FakeAnthropic("x", usage=_Usage(input_tokens=500, output_tokens=50))
    asyncio.run(prov.complete("s", "p"))
    assert acct.cost_for(11) == pytest.approx(
        price_for_usage(DEFAULT_MODEL, input_tokens=500, output_tokens=50)
    )
    assert acct.clients() == [11]
