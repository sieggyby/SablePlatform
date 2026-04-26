"""Tests for sable_platform.checkin.render — deterministic, no LLM."""
from __future__ import annotations

from sable_platform.checkin.collector import CheckinInputs
from sable_platform.checkin.deltas import compute_deltas
from sable_platform.checkin.render import render_data_sections


def _inputs(**overrides) -> CheckinInputs:
    base = CheckinInputs(
        org_id="tig",
        run_date="2026-05-01",
        tier1={
            "fletcher_followers": 3457,
            "tig_followers": 8538,
            "discord_active_posters_weekly": None,
            "discord_retention_delta": None,
            "twitter_mentions": 1807,
        },
        tier2={
            "team_reply_rate": 0.0047,
            "lateral_reply_count": 0,
            "recurring_engaged_accounts": 282,
            "named_subsquads_publicly": None,
        },
        previous_metrics={
            "tier1": {"tig_followers": 8500, "fletcher_followers": 3450, "twitter_mentions": 1800},
            "tier2": {"team_reply_rate": 0.005, "lateral_reply_count": 0, "recurring_engaged_accounts": 280},
        },
        previous_snapshot_date="2026-04-24",
        cult_grader_meta={"run_id": "r-abc", "run_date": "2026-04-30"},
        actions_this_week=[
            {"title": "Reply to name-tag holders", "status": "completed", "source": "playbook",
             "completed_at": "2026-04-30 14:00", "claimed_at": None, "created_at": "2026-04-28"},
            {"title": "DM Fletcher about ritual cadence", "status": "pending", "source": "manual",
             "completed_at": None, "claimed_at": None, "created_at": "2026-04-29"},
        ],
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_render_returns_four_sections():
    inputs = _inputs()
    deltas = compute_deltas(inputs.tier1, inputs.tier2, inputs.previous_metrics)
    out = render_data_sections(inputs, deltas)
    assert set(out.keys()) == {"header", "tier1_table", "tier2_table", "tier3_table"}


def test_header_includes_run_date_and_baseline():
    inputs = _inputs()
    deltas = compute_deltas(inputs.tier1, inputs.tier2, inputs.previous_metrics)
    header = render_data_sections(inputs, deltas)["header"]
    assert "2026-05-01" in header
    assert "2026-04-24" in header
    assert "r-abc" in header


def test_header_marks_first_run_when_no_baseline():
    inputs = _inputs(previous_snapshot_date=None, previous_metrics={})
    deltas = compute_deltas(inputs.tier1, inputs.tier2, None)
    header = render_data_sections(inputs, deltas)["header"]
    assert "first check-in" in header


def test_tier1_table_shape_and_arrows():
    inputs = _inputs()
    deltas = compute_deltas(inputs.tier1, inputs.tier2, inputs.previous_metrics)
    table = render_data_sections(inputs, deltas)["tier1_table"]
    assert "### Tier 1" in table
    assert "Fletcher followers" in table
    assert "TIG followers" in table
    assert "▲" in table  # at least one up
    # discord rows render as em-dash placeholders
    assert "Discord active posters" in table
    assert "Discord retention" in table


def test_team_reply_rate_renders_as_percent():
    inputs = _inputs()
    deltas = compute_deltas(inputs.tier1, inputs.tier2, inputs.previous_metrics)
    table = render_data_sections(inputs, deltas)["tier2_table"]
    # 0.0047 → 0.47%
    assert "0.47%" in table


def test_actions_table_includes_counts_and_titles():
    inputs = _inputs()
    deltas = compute_deltas(inputs.tier1, inputs.tier2, inputs.previous_metrics)
    table = render_data_sections(inputs, deltas)["tier3_table"]
    assert "Sable activity" in table
    assert "completed: 1" in table
    assert "pending: 1" in table
    assert "Reply to name-tag holders" in table
    assert "DM Fletcher about ritual cadence" in table


def test_actions_table_handles_empty():
    inputs = _inputs(actions_this_week=[])
    deltas = compute_deltas(inputs.tier1, inputs.tier2, inputs.previous_metrics)
    table = render_data_sections(inputs, deltas)["tier3_table"]
    assert "No actions logged this week" in table
