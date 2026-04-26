"""Tests for sable_platform.checkin.synthesize.

No real network calls — every test passes a mock client whose .messages.create
returns a stub response object shaped like the Anthropic SDK's Message.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sable_platform.checkin.collector import CheckinInputs
from sable_platform.checkin.deltas import compute_deltas
from sable_platform.checkin.render import render_data_sections
from sable_platform.checkin.synthesize import (
    DEFAULT_MODEL,
    SYSTEM_PROMPT,
    SynthesisResult,
    _compute_cost_usd,
    _split_sections,
    synthesize,
)
from sable_platform.errors import SableError, INVALID_CONFIG, STEP_EXECUTION_ERROR


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _inputs() -> CheckinInputs:
    return CheckinInputs(
        org_id="tig",
        run_date="2026-05-01",
        tier1={"fletcher_followers": 3457, "tig_followers": 8538,
               "discord_joins": None, "discord_velocity": None, "twitter_mentions": 1807},
        tier2={"team_reply_rate": 0.0047, "lateral_reply_count": 0,
               "recurring_engaged_accounts": 282, "named_subsquads_publicly": None},
        previous_metrics={
            "tier1": {"tig_followers": 8500, "fletcher_followers": 3450},
            "tier2": {"team_reply_rate": 0.005},
        },
        previous_snapshot_date="2026-04-24",
        cult_grader_meta={"run_id": "r-abc", "run_date": "2026-04-30"},
        actions_this_week=[
            {"title": "Reply to name-tag holders", "status": "completed", "source": "playbook",
             "completed_at": "2026-04-30 14:00", "claimed_at": None, "created_at": "2026-04-28"},
        ],
    )


def _stub_message(text: str, *, in_tok=400, out_tok=120, cache_create=0, cache_read=0):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_creation_input_tokens=cache_create,
            cache_read_input_tokens=cache_read,
        ),
    )


def _mock_client(response_text: str, **usage):
    client = MagicMock()
    client.messages.create.return_value = _stub_message(response_text, **usage)
    return client


# ---------------------------------------------------------------------------
# _split_sections
# ---------------------------------------------------------------------------

def test_split_sections_canonical():
    raw = "## SUMMARY\n- bullet one\n- bullet two\n\n---\n## DEEP_DIVE\n### Tier 1\nbody."
    s, d = _split_sections(raw)
    assert s.startswith("## SUMMARY")
    assert d.startswith("## DEEP_DIVE")


def test_split_sections_fallback_on_header():
    raw = "## SUMMARY\n- bullet\n## DEEP_DIVE\n### Tier 1\nbody."
    s, d = _split_sections(raw)
    assert "bullet" in s
    assert d.startswith("## DEEP_DIVE")


def test_split_sections_lone_block():
    raw = "Just one section, no separator."
    s, d = _split_sections(raw)
    assert s == "Just one section, no separator."
    assert d == ""


# ---------------------------------------------------------------------------
# _compute_cost_usd
# ---------------------------------------------------------------------------

def test_compute_cost_usd_standard_call():
    # 1000 input + 500 output for opus-4-7
    # 1000/1M * $15 + 500/1M * $75 = 0.015 + 0.0375 = 0.0525
    cost = _compute_cost_usd("claude-opus-4-7", 1000, 500, 0, 0)
    assert cost == pytest.approx(0.0525)


def test_compute_cost_usd_with_cache():
    # 200 cache write + 800 cache read + 100 input + 100 output
    # write: 200/1M * 18.75 = 0.00375
    # read:  800/1M * 1.5  = 0.0012
    # input: 100/1M * 15   = 0.0015
    # out:   100/1M * 75   = 0.0075
    cost = _compute_cost_usd("claude-opus-4-7", 100, 100, 200, 800)
    assert cost == pytest.approx(0.01395, abs=1e-5)


def test_compute_cost_usd_unknown_model_falls_back():
    cost = _compute_cost_usd("nonexistent-model", 1000, 500, 0, 0)
    # Should match opus-4-7 fallback
    assert cost == pytest.approx(0.0525)


# ---------------------------------------------------------------------------
# synthesize() — full path with mock client
# ---------------------------------------------------------------------------

def test_synthesize_parses_two_sections():
    inputs = _inputs()
    deltas = compute_deltas(inputs.tier1, inputs.tier2, inputs.previous_metrics)
    sections = render_data_sections(inputs, deltas)
    canned = (
        "## SUMMARY\n- Recurring engaged accounts held at 282.\n- Three drafts shipped.\n"
        "\n---\n"
        "## DEEP_DIVE\n### Tier 1\n...\n### Tier 2\n...\n### Tier 3\n..."
    )
    client = _mock_client(canned, in_tok=500, out_tok=200, cache_create=600, cache_read=0)

    result = synthesize(inputs, deltas, sections, client=client)

    assert isinstance(result, SynthesisResult)
    assert "Recurring engaged accounts" in result.summary_prose
    assert result.deep_dive_prose.startswith("## DEEP_DIVE")
    assert result.model == DEFAULT_MODEL
    assert result.input_tokens == 500
    assert result.output_tokens == 200
    assert result.cache_creation_input_tokens == 600
    assert result.cost_usd > 0


def test_synthesize_passes_cached_system_prompt():
    inputs = _inputs()
    deltas = compute_deltas(inputs.tier1, inputs.tier2, inputs.previous_metrics)
    sections = render_data_sections(inputs, deltas)
    client = _mock_client("## SUMMARY\nbody\n---\n## DEEP_DIVE\nbody")

    synthesize(inputs, deltas, sections, client=client)

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == DEFAULT_MODEL
    assert isinstance(kwargs["system"], list) and len(kwargs["system"]) == 1
    sysblock = kwargs["system"][0]
    assert sysblock["text"] == SYSTEM_PROMPT
    assert sysblock["cache_control"] == {"type": "ephemeral"}


def test_synthesize_user_prompt_includes_data_tables():
    inputs = _inputs()
    deltas = compute_deltas(inputs.tier1, inputs.tier2, inputs.previous_metrics)
    sections = render_data_sections(inputs, deltas)
    client = _mock_client("## SUMMARY\nx\n---\n## DEEP_DIVE\ny")

    synthesize(inputs, deltas, sections, client=client)

    user_msg = client.messages.create.call_args.kwargs["messages"][0]
    assert user_msg["role"] == "user"
    assert "Tier 1" in user_msg["content"]
    assert "Reply to name-tag holders" in user_msg["content"]
    assert "tig" in user_msg["content"]


def test_synthesize_wraps_api_error_in_sableerror():
    inputs = _inputs()
    deltas = compute_deltas(inputs.tier1, inputs.tier2, inputs.previous_metrics)
    sections = render_data_sections(inputs, deltas)
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("rate limit")

    with pytest.raises(SableError) as exc:
        synthesize(inputs, deltas, sections, client=client)
    assert exc.value.code == STEP_EXECUTION_ERROR


def test_synthesize_no_api_key_raises_invalid_config(monkeypatch):
    inputs = _inputs()
    deltas = compute_deltas(inputs.tier1, inputs.tier2, inputs.previous_metrics)
    sections = render_data_sections(inputs, deltas)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(SableError) as exc:
        synthesize(inputs, deltas, sections)  # no client → tries to build one
    assert exc.value.code == INVALID_CONFIG
