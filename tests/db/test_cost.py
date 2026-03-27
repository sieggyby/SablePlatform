"""Tests for db/cost.py — cost logging and budget enforcement."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from sable_platform.db.cost import (
    log_cost,
    get_weekly_spend,
    get_org_cost_cap,
    check_budget,
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
