"""Step 4 of client_checkin_loop: synthesize prose with Opus 4.7.

Single Anthropic call. The system prompt is cached (cache_control on the
last block) so subsequent weekly runs share the prefix and pay only for the
delta. The user prompt carries the rendered data tables + raw inputs — the
model picks 2-3 standout deltas to highlight in prose, never inventing numbers.

Architecture deviation: this is the first direct LLM dep on the platform.
Contained to checkin/. Migrates to Sable_Client_Comms post-trial.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from sable_platform.checkin.collector import CheckinInputs
from sable_platform.checkin.deltas import DeltaReport
from sable_platform.errors import SableError, INVALID_CONFIG, STEP_EXECUTION_ERROR

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_MAX_TOKENS = 2048

# Pricing per 1M tokens (Opus 4.7 — current as of 2026-04-26).
# Cache reads are 1/10 of input price; cache writes are 1.25x input price.
_PRICE_PER_MTOK = {
    "claude-opus-4-7": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
}

SYSTEM_PROMPT = """You are Sable's weekly check-in writer for one paying client.

The client is reading a Telegram message that the operator (Sieggy) reviews
and forwards on. You write the prose; deterministic data tables are appended
verbatim by the assembler. Never repeat raw numbers from the tables — you
may reference 2-3 of them in the bullets and prose, but quote the table, do
not retype it.

Output two sections, separated by a single line containing exactly `---`:

## SUMMARY (first TG message — fits in one phone screen)
Three to five bullets. The first bullet must reference one specific datum
from the tables (e.g. a named entity, a Δ direction, an artifact type) so
the reader knows you actually looked at this week's data. No generic praise.

## DEEP_DIVE (second TG message)
Three short paragraphs in this order, with the matching `### Tier 1`,
`### Tier 2`, `### Tier 3` headers:
- Tier 1: what TIG's stated 5 numbers did, and one short interpretation
- Tier 2: what Sable-influenceable indicators did, and what we tried this week
- Tier 3: what Sable shipped/skipped, why, and what's queued for next week

Constraints:
- Honest about no_baseline rows — say "first check-in baseline" rather than
  manufacturing a trend.
- If a metric is `—` (unavailable) call it out once, in the relevant tier's
  paragraph, with the reason the tables note (e.g. Discord pulse not yet wired).
- No emojis, no marketing language, no exclamation marks.
- Total prose budget: ~250 words across both sections."""


@dataclass
class SynthesisResult:
    summary_prose: str
    deep_dive_prose: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    cost_usd: float
    raw_text: str


def _build_user_prompt(
    inputs: CheckinInputs,
    deltas: DeltaReport,
    data_sections: dict[str, str],
) -> str:
    actions_blob = "\n".join(
        f"- [{a.get('status')}] {a.get('title')} (source={a.get('source')})"
        for a in inputs.actions_this_week
    ) or "_none_"

    return f"""Org: {inputs.org_id}
Run date: {inputs.run_date}
Last baseline: {inputs.previous_snapshot_date or "none — first check-in"}

{data_sections["header"]}

{data_sections["tier1_table"]}

{data_sections["tier2_table"]}

{data_sections["tier3_table"]}

Raw delta JSON (for your reference, do not transcribe):
{deltas.as_dict()}

Actions touched this week:
{actions_blob}

Now write SUMMARY and DEEP_DIVE per the system prompt."""


def _split_sections(raw: str) -> tuple[str, str]:
    """Split on the first `---` line. Tolerant of leading/trailing headers."""
    parts = raw.split("\n---\n", 1)
    if len(parts) == 1:
        # Fall back: split on `## DEEP_DIVE` if present.
        if "## DEEP_DIVE" in raw:
            sa, sb = raw.split("## DEEP_DIVE", 1)
            return sa.strip(), ("## DEEP_DIVE" + sb).strip()
        # Last resort: single block as summary.
        return raw.strip(), ""
    summary, deep_dive = parts[0].strip(), parts[1].strip()
    return summary, deep_dive


def _compute_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation: int,
    cache_read: int,
) -> float:
    rates = _PRICE_PER_MTOK.get(model, _PRICE_PER_MTOK[DEFAULT_MODEL])
    return round(
        (input_tokens / 1_000_000) * rates["input"]
        + (output_tokens / 1_000_000) * rates["output"]
        + (cache_creation / 1_000_000) * rates["cache_write"]
        + (cache_read / 1_000_000) * rates["cache_read"],
        6,
    )


def _make_client():
    """Lazy import + construction. Raises SableError if no API key."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SableError(
            INVALID_CONFIG,
            "ANTHROPIC_API_KEY not set — required by sable_platform.checkin.synthesize",
        )
    import anthropic  # local import keeps test paths free of network deps
    return anthropic.Anthropic(api_key=api_key)


def synthesize(
    inputs: CheckinInputs,
    deltas: DeltaReport,
    data_sections: dict[str, str],
    *,
    client: Any | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> SynthesisResult:
    """Single Anthropic call. Returns parsed prose + usage + cost.

    The caller (workflow step) is responsible for ``log_cost`` so cost logging
    happens inside the same DB transaction as the workflow_step row.
    """
    if client is None:
        client = _make_client()

    user_prompt = _build_user_prompt(inputs, deltas, data_sections)

    try:
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:
        raise SableError(STEP_EXECUTION_ERROR, f"Anthropic call failed: {exc}") from exc

    raw = "".join(
        block.text for block in message.content
        if getattr(block, "type", None) == "text"
    )
    summary, deep_dive = _split_sections(raw)

    usage = message.usage
    in_tok = getattr(usage, "input_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cost = _compute_cost_usd(model, in_tok, out_tok, cache_create, cache_read)

    return SynthesisResult(
        summary_prose=summary,
        deep_dive_prose=deep_dive,
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_creation_input_tokens=cache_create,
        cache_read_input_tokens=cache_read,
        cost_usd=cost,
        raw_text=raw,
    )
