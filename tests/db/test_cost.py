"""Tests for db/cost.py — cost logging and budget enforcement."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import datetime

from sable_platform.db.cost import (
    get_org_ambient_daily_cap,
    log_cost,
    get_weekly_spend,
    get_org_cost_cap,
    check_budget,
    get_daily_spend,
    get_org_image_daily_cap,
    _read_platform_config,
)
from sable_platform.errors import SableError, BUDGET_EXCEEDED


def test_log_cost_inserts_row(in_memory_db):
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('org1', 'Org One')")
    conn.commit()

    log_cost(conn, "org1", "llm_call", 0.05, model="claude-3", input_tokens=100, output_tokens=50)

    row = conn.execute("SELECT * FROM cost_events WHERE org_id='org1'").fetchone()
    assert row is not None
    assert row["cost_usd"] == pytest.approx(0.05)
    assert row["call_type"] == "llm_call"
    assert row["model"] == "claude-3"
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 50


def test_get_weekly_spend_sums_current_week(in_memory_db):
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('org2', 'Org Two')")
    conn.commit()

    # Two events in current week (no explicit created_at → defaults to now)
    log_cost(conn, "org2", "llm_call", 1.00)
    log_cost(conn, "org2", "llm_call", 2.50)

    # One old event far in the past
    conn.execute(
        """INSERT INTO cost_events (org_id, call_type, cost_usd, call_status, created_at)
           VALUES ('org2', 'llm_call', 99.99, 'success', '2020-01-06 00:00:00')"""
    )
    conn.commit()

    spend = get_weekly_spend(conn, "org2")
    assert spend == pytest.approx(3.50)


def test_get_weekly_spend_zero_for_new_org(in_memory_db):
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('org3', 'Org Three')")
    conn.commit()

    spend = get_weekly_spend(conn, "org3")
    assert spend == pytest.approx(0.0)


def test_get_org_cost_cap_from_db_config(in_memory_db):
    conn = in_memory_db
    cfg = json.dumps({"max_ai_usd_per_org_per_week": 10.0})
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, config_json) VALUES ('org4', 'Org Four', ?)",
        (cfg,),
    )
    conn.commit()

    cap = get_org_cost_cap(conn, "org4")
    assert cap == pytest.approx(10.0)


def test_get_org_cost_cap_default_fallback(in_memory_db):
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('org5', 'Org Five')")
    conn.commit()

    with patch("sable_platform.db.cost._read_platform_config", return_value={}):
        cap = get_org_cost_cap(conn, "org5")

    assert cap == pytest.approx(5.00)


def test_check_budget_raises_when_exceeded(in_memory_db):
    conn = in_memory_db
    cfg = json.dumps({"max_ai_usd_per_org_per_week": 1.00})
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, config_json) VALUES ('org6', 'Org Six', ?)",
        (cfg,),
    )
    conn.commit()

    log_cost(conn, "org6", "llm_call", 1.00)  # exactly at cap → raises

    with pytest.raises(SableError) as exc_info:
        check_budget(conn, "org6")

    assert exc_info.value.code == BUDGET_EXCEEDED


def test_check_budget_returns_spend_and_cap_under_limit(in_memory_db):
    conn = in_memory_db
    cfg = json.dumps({"max_ai_usd_per_org_per_week": 10.00})
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, config_json) VALUES ('org7', 'Org Seven', ?)",
        (cfg,),
    )
    conn.commit()

    log_cost(conn, "org7", "llm_call", 2.00)

    spend, cap = check_budget(conn, "org7")
    assert spend == pytest.approx(2.00)
    assert cap == pytest.approx(10.00)


def test_check_budget_warns_at_90_percent(in_memory_db):
    """At 91% of cap, warning is logged but no exception raised."""
    conn = in_memory_db
    cfg = json.dumps({"max_ai_usd_per_org_per_week": 10.00})
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, config_json) VALUES ('org8', 'Org Eight', ?)",
        (cfg,),
    )
    conn.commit()

    log_cost(conn, "org8", "llm_call", 9.10)  # 91% of 10.00

    spend, cap = check_budget(conn, "org8")  # must not raise
    assert spend == pytest.approx(9.10)
    assert cap == pytest.approx(10.00)


_UTC = datetime.timezone.utc


def test_get_daily_spend_window_and_call_type_filter(in_memory_db):
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('orgD', 'Org D')")
    # two meme_image charges TODAY + one llm_call today + one meme_image YESTERDAY
    for ct, cost, ts in [
        ("meme_image", 0.05, "2026-06-23 10:00:00"),
        ("meme_image", 0.03, "2026-06-23 11:00:00"),
        ("llm_call", 2.00, "2026-06-23 12:00:00"),
        ("meme_image", 1.00, "2026-06-22 23:00:00"),
    ]:
        conn.execute(
            "INSERT INTO cost_events (org_id, call_type, cost_usd, call_status, created_at) "
            "VALUES ('orgD', ?, ?, 'success', ?)",
            (ct, cost, ts),
        )
    conn.commit()
    now = datetime.datetime(2026, 6, 23, 15, 0, 0, tzinfo=_UTC)
    # call_type filter: only today's meme_image (yesterday + llm_call excluded)
    assert get_daily_spend(conn, "orgD", call_type="meme_image", now=now) == pytest.approx(0.08)
    # no filter: all of today (yesterday's row still excluded by the day window)
    assert get_daily_spend(conn, "orgD", now=now) == pytest.approx(2.08)


def test_get_daily_spend_zero_for_new_org(in_memory_db):
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('orgE', 'Org E')")
    conn.commit()
    assert get_daily_spend(conn, "orgE", call_type="meme_image") == pytest.approx(0.0)


def test_get_org_image_daily_cap_from_db_config(in_memory_db):
    conn = in_memory_db
    cfg = json.dumps({"max_image_usd_per_org_per_day": 3.0})
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, config_json) VALUES ('orgF', 'Org F', ?)",
        (cfg,),
    )
    conn.commit()
    assert get_org_image_daily_cap(conn, "orgF") == pytest.approx(3.0)


def test_get_org_image_daily_cap_default_below_weekly(in_memory_db):
    # The default MUST be below the weekly default ($5) or the daily cap is a strict no-op
    # (daily image spend is a subset of weekly AI spend).
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('orgG', 'Org G')")
    conn.commit()
    with patch("sable_platform.db.cost._read_platform_config", return_value={}):
        cap = get_org_image_daily_cap(conn, "orgG")
    assert cap == pytest.approx(2.00)
    assert cap < 5.00  # the property that makes it bind


def test_get_daily_spend_call_type_prefix_family(in_memory_db):
    # The ambient producer's daily cap sums the whole 'ambient.' tag family — and ONLY it.
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('orgP', 'Org P')")
    for ct, cost in [
        ("ambient.meme_ideate", 0.30),
        ("ambient.write_variants", 0.05),
        ("meme_ideate", 0.40),        # operator Generate click — NOT ambient
        ("write_variants", 0.10),     # operator compose — NOT ambient
    ]:
        conn.execute(
            "INSERT INTO cost_events (org_id, call_type, cost_usd, call_status, created_at) "
            "VALUES ('orgP', ?, ?, 'success', '2026-06-23 10:00:00')",
            (ct, cost),
        )
    conn.commit()
    now = datetime.datetime(2026, 6, 23, 15, 0, 0, tzinfo=_UTC)
    assert get_daily_spend(conn, "orgP", call_type_prefix="ambient.", now=now) == pytest.approx(0.35)


def test_get_daily_spend_prefix_and_call_type_mutually_exclusive(in_memory_db):
    conn = in_memory_db
    with pytest.raises(ValueError):
        get_daily_spend(conn, "orgP", call_type="x", call_type_prefix="y")


def test_get_daily_spend_prefix_escapes_like_wildcards(in_memory_db):
    # A literal '%'/'_' in the prefix must match literally, never as a LIKE wildcard.
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('orgQ', 'Org Q')")
    for ct in ("ambient_meme", "ambientXmeme"):  # '_' as wildcard would match BOTH
        conn.execute(
            "INSERT INTO cost_events (org_id, call_type, cost_usd, call_status, created_at) "
            "VALUES ('orgQ', ?, 0.10, 'success', '2026-06-23 10:00:00')",
            (ct,),
        )
    conn.commit()
    now = datetime.datetime(2026, 6, 23, 15, 0, 0, tzinfo=_UTC)
    assert get_daily_spend(conn, "orgQ", call_type_prefix="ambient_", now=now) == pytest.approx(0.10)


def test_get_org_ambient_daily_cap_from_db_config(in_memory_db):
    conn = in_memory_db
    cfg = json.dumps({"max_ambient_usd_per_org_per_day": 0.25})
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, config_json) VALUES ('orgR', 'Org R', ?)",
        (cfg,),
    )
    conn.commit()
    assert get_org_ambient_daily_cap(conn, "orgR") == pytest.approx(0.25)


@pytest.mark.parametrize("blob", ["[]", '"a string"', "42"])
def test_cap_accessors_degrade_on_non_dict_config_json(in_memory_db, blob):
    # Codex FIX: valid-but-non-dict JSON ('[]') used to raise AttributeError through
    # cfg.get and crash the budget gate; every cap accessor must fall back to defaults.
    from sable_platform.db.cost import get_org_cost_cap
    conn = in_memory_db
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, config_json) VALUES ('orgT', 'Org T', ?)",
        (blob,),
    )
    conn.commit()
    with patch("sable_platform.db.cost._read_platform_config", return_value={}):
        assert get_org_ambient_daily_cap(conn, "orgT") == pytest.approx(1.00)
        assert get_org_cost_cap(conn, "orgT") == pytest.approx(5.00)
        assert get_org_image_daily_cap(conn, "orgT") == pytest.approx(2.00)


def test_get_org_ambient_daily_cap_default_below_weekly(in_memory_db):
    # Same binding property as the image cap: the $1/day default must sit below the $5/week
    # default or a nightly producer could consume the whole week's budget.
    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('orgS', 'Org S')")
    conn.commit()
    with patch("sable_platform.db.cost._read_platform_config", return_value={}):
        cap = get_org_ambient_daily_cap(conn, "orgS")
    assert cap == pytest.approx(1.00)
    assert cap < 5.00


def test_get_daily_spend_naive_now_treated_as_utc(in_memory_db):
    # A tz-naive `now` must be read as UTC, not converted from local (a midnight-window footgun).
    # Pin a UTC-AHEAD local tz so the pre-fix `astimezone(local)` bug WOULD shift the day window
    # and drop the event -> the assertion fails against the old code on ANY host, not just a
    # UTC-ahead one (terminator DC-2-test-note hardening).
    import os
    import time

    conn = in_memory_db
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('orgH', 'Org H')")
    conn.execute(
        "INSERT INTO cost_events (org_id, call_type, cost_usd, call_status, created_at) "
        "VALUES ('orgH', 'meme_image', 0.05, 'success', '2026-06-23 01:00:00')"
    )
    conn.commit()
    # naive 06-23 02:00: as UTC -> day 06-23 (event in window); as Kolkata local(+5:30) -> UTC
    # 06-22 20:30 -> day 06-22 (event OUT). aware is unambiguous.
    naive = datetime.datetime(2026, 6, 23, 2, 0, 0)
    aware = datetime.datetime(2026, 6, 23, 2, 0, 0, tzinfo=_UTC)
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Kolkata"  # UTC+5:30
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        assert get_daily_spend(conn, "orgH", now=naive) == pytest.approx(0.05)  # fails vs the bug
        assert get_daily_spend(conn, "orgH", now=aware) == pytest.approx(0.05)
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        if hasattr(time, "tzset"):
            time.tzset()
