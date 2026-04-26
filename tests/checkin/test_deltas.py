"""Tests for sable_platform.checkin.deltas — pure function, no DB."""
from __future__ import annotations

from sable_platform.checkin.deltas import compute_deltas


def test_no_baseline_returns_no_baseline_direction():
    cur_t1 = {"tig_followers": 8538, "fletcher_followers": 3457}
    cur_t2 = {"team_reply_rate": 0.0047}
    report = compute_deltas(cur_t1, cur_t2, previous_metrics=None)

    keys = {d.key for d in report.tier1}
    assert keys == {"tig_followers", "fletcher_followers"}
    for d in report.tier1:
        assert d.direction == "no_baseline"
        assert d.delta is None
        assert d.pct_change is None


def test_up_delta_with_pct_change():
    report = compute_deltas(
        {"tig_followers": 8600},
        {},
        previous_metrics={"tier1": {"tig_followers": 8538}, "tier2": {}},
    )
    d = report.tier1[0]
    assert d.direction == "up"
    assert d.delta == 62.0
    assert d.pct_change == round(62 / 8538, 4)


def test_down_delta_negative_pct():
    report = compute_deltas(
        {"twitter_mentions": 1700},
        {},
        previous_metrics={"tier1": {"twitter_mentions": 1807}, "tier2": {}},
    )
    d = report.tier1[0]
    assert d.direction == "down"
    assert d.delta == -107.0
    assert d.pct_change is not None and d.pct_change < 0


def test_flat_when_equal():
    report = compute_deltas(
        {"recurring_engaged_accounts": 282},
        {},
        previous_metrics={"tier1": {}, "tier2": {}},
    )
    # current value present, prev missing → no_baseline (not flat)
    assert report.tier1[0].direction == "no_baseline"

    report2 = compute_deltas(
        {"recurring_engaged_accounts": 282},
        {},
        previous_metrics={"tier1": {"recurring_engaged_accounts": 282}, "tier2": {}},
    )
    assert report2.tier1[0].direction == "flat"
    assert report2.tier1[0].delta == 0.0


def test_zero_previous_yields_none_pct_but_up_direction():
    report = compute_deltas(
        {},
        {"lateral_reply_count": 5},
        previous_metrics={"tier1": {}, "tier2": {"lateral_reply_count": 0}},
    )
    d = report.tier2[0]
    assert d.direction == "up"
    assert d.delta == 5.0
    assert d.pct_change is None  # division by zero protected


def test_non_numeric_treated_as_no_baseline():
    report = compute_deltas(
        {"named_subsquads_publicly": None},
        {},
        previous_metrics={"tier1": {"named_subsquads_publicly": None}, "tier2": {}},
    )
    assert report.tier1[0].direction == "no_baseline"


def test_as_dict_round_trip():
    report = compute_deltas(
        {"tig_followers": 100},
        {},
        previous_metrics={"tier1": {"tig_followers": 80}, "tier2": {}},
    )
    payload = report.as_dict()
    assert payload["tier1"][0]["direction"] == "up"
    assert payload["tier1"][0]["delta"] == 20.0
    assert payload["tier2"] == []
