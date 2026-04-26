"""Step 3 of client_checkin_loop: render data sections (deterministic, no LLM).

The synthesize step (Task 7) gets these tables in its prompt — the LLM never
generates raw numbers, it just picks two or three to highlight in prose. This
keeps the data side falsifiable and the model on rails.
"""
from __future__ import annotations

from typing import Any

from sable_platform.checkin.collector import CheckinInputs
from sable_platform.checkin.deltas import DeltaReport, MetricDelta


_TIER1_LABELS = {
    "fletcher_followers": "Fletcher followers",
    "tig_followers": "TIG followers",
    "discord_active_posters_weekly": "Discord active posters (7d)",
    "discord_retention_delta": "Discord retention Δ (WoW)",
    "twitter_mentions": "Twitter unique mentioners",
}

_TIER2_LABELS = {
    "team_reply_rate": "Team reply rate",
    "lateral_reply_count": "Lateral reply count",
    "recurring_engaged_accounts": "Recurring engaged accounts",
    "named_subsquads_publicly": "Named subsquads (public)",
}

_RATE_KEYS = {"team_reply_rate"}


def _fmt_value(key: str, value: Any) -> str:
    if value is None:
        return "—"
    if key in _RATE_KEYS and isinstance(value, (int, float)):
        return f"{value * 100:.2f}%"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}"
    return str(value)


def _fmt_delta(d: MetricDelta) -> str:
    if d.direction == "no_baseline":
        return "(no baseline)"
    if d.direction == "flat":
        return "→ 0"
    arrow = "▲" if d.direction == "up" else "▼"
    abs_part = _fmt_value(d.key, abs(d.delta) if d.delta is not None else None)
    if d.pct_change is None:
        return f"{arrow} {abs_part}"
    return f"{arrow} {abs_part} ({d.pct_change * 100:+.1f}%)"


def _render_metric_table(
    title: str,
    deltas: list[MetricDelta],
    labels: dict[str, str],
) -> str:
    lines = [
        f"### {title}",
        "",
        "| Metric | This week | Last week | Δ |",
        "|---|---|---|---|",
    ]
    for d in deltas:
        label = labels.get(d.key, d.key)
        cur = _fmt_value(d.key, d.current)
        prev = _fmt_value(d.key, d.previous) if d.direction != "no_baseline" else "—"
        lines.append(f"| {label} | {cur} | {prev} | {_fmt_delta(d)} |")
    return "\n".join(lines)


def _render_actions_table(actions: list[dict]) -> str:
    if not actions:
        return "### Sable activity\n\n_No actions logged this week._"

    lines = [
        "### Sable activity",
        "",
        "| Status | Title | Source | Updated |",
        "|---|---|---|---|",
    ]
    counts = {"completed": 0, "claimed": 0, "skipped": 0, "pending": 0}
    for a in actions:
        status = a.get("status", "?")
        counts[status] = counts.get(status, 0) + 1
        title = (a.get("title") or "").strip().replace("|", "\\|")
        source = a.get("source") or "—"
        updated = a.get("completed_at") or a.get("claimed_at") or a.get("created_at") or "—"
        lines.append(f"| {status} | {title} | {source} | {updated} |")

    summary = ", ".join(f"{k}: {v}" for k, v in counts.items() if v)
    lines.insert(2, f"_Counts — {summary}_")
    lines.insert(3, "")
    return "\n".join(lines)


def render_data_sections(inputs: CheckinInputs, deltas: DeltaReport) -> dict[str, str]:
    """Return deterministic markdown blocks the synthesizer + assembler share.

    Keys:
      - tier1_table : Tier 1 (TIG's stated 5)
      - tier2_table : Tier 2 (Sable-influenceable)
      - tier3_table : Sable activity (actions this week)
      - header     : run-date / cult_grader-run-id stamp
    """
    cg = inputs.cult_grader_meta
    header_lines = [
        f"**Check-in date:** {inputs.run_date}",
        f"**Cult Grader run:** `{cg.get('run_id') or 'n/a'}` ({cg.get('run_date') or 'n/a'})",
    ]
    if inputs.previous_snapshot_date:
        header_lines.append(f"**Last week's baseline:** {inputs.previous_snapshot_date}")
    else:
        header_lines.append("**Last week's baseline:** _none — first check-in_")

    return {
        "header": "\n".join(header_lines),
        "tier1_table": _render_metric_table("Tier 1 — TIG's stated metrics", deltas.tier1, _TIER1_LABELS),
        "tier2_table": _render_metric_table(
            "Tier 2 — Sable-influenceable leading indicators", deltas.tier2, _TIER2_LABELS
        ),
        "tier3_table": _render_actions_table(inputs.actions_this_week),
    }
