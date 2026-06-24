"""Tests for the per-operator meme-production dollar budget (migration 078)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from sable_platform.db.connection import get_db
from sable_platform.db.meme_budget import (
    DEFAULT_MEME_WEEKLY_CAP_USD,
    meme_weekly_cap,
    operator_meme_status,
    reconcile_meme_spend,
    reserve_meme_spend,
    week_iso,
)

_NOW = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)        # ISO 2026-W26
_NEXT_WEEK = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)  # ISO 2026-W27


@pytest.fixture
def conn(tmp_path):
    c = get_db(db_path=str(tmp_path / "sable.db"))
    c.execute("INSERT INTO orgs (org_id, display_name, status) VALUES (?, ?, ?)",
              ("tig", "TIG", "active"))
    c.commit()
    yield c
    c.close()


def test_week_iso_boundary():
    assert week_iso(_NOW) == "2026-W26"
    assert week_iso(_NEXT_WEEK) == "2026-W27"


def test_default_cap_when_no_override(conn):
    assert meme_weekly_cap(conn, "tig") == DEFAULT_MEME_WEEKLY_CAP_USD == 5.00
    # an unknown org also gets the default (no row -> default, never raises)
    assert meme_weekly_cap(conn, "ghost") == 5.00


def test_config_override_cap(conn):
    conn.execute("UPDATE orgs SET config_json = ? WHERE org_id = ?",
                 (json.dumps({"max_meme_usd_per_operator_per_week": 2.0}), "tig"))
    conn.commit()
    assert meme_weekly_cap(conn, "tig") == 2.0


def test_bad_override_falls_back_to_default(conn):
    conn.execute("UPDATE orgs SET config_json = ? WHERE org_id = ?",
                 (json.dumps({"max_meme_usd_per_operator_per_week": "lots"}), "tig"))
    conn.commit()
    assert meme_weekly_cap(conn, "tig") == 5.00


def test_status_empty_is_full_budget(conn):
    st = operator_meme_status(conn, "operator_sieg", "tig", now=_NOW)
    assert st["spend_usd"] == 0.0 and st["runs"] == 0
    assert st["remaining_usd"] == 5.0 and st["allowed"] is True


def test_reserve_then_reconcile_records_actual(conn):
    r = reserve_meme_spend(conn, "operator_sieg", "tig", estimate=0.15, now=_NOW)
    conn.commit()
    assert r["allowed"] is True and r["estimate"] == 0.15
    assert r["spend_usd"] == 0.15 and r["runs"] == 1     # estimate held up front
    reconcile_meme_spend(conn, "operator_sieg", "tig", estimate=0.15, actual=0.08, now=_NOW)
    conn.commit()
    st = operator_meme_status(conn, "operator_sieg", "tig", now=_NOW)
    assert st["spend_usd"] == 0.08                       # reconciled down to the real cost
    assert st["runs"] == 1 and st["remaining_usd"] == round(5.0 - 0.08, 4)


def test_reconcile_records_actual_above_estimate(conn):
    # Defensive: the DEFAULT estimate (0.30) is meant to stay >= a real call so reconcile only
    # lowers spend. But if a call somehow cost MORE than the held estimate, reconcile records the
    # TRUE spend (the next reserve then sees it and refuses) — never silently drops the overage.
    reserve_meme_spend(conn, "op", "tig", estimate=0.15, now=_NOW)
    reconcile_meme_spend(conn, "op", "tig", estimate=0.15, actual=0.40, now=_NOW)
    conn.commit()
    assert operator_meme_status(conn, "op", "tig", now=_NOW)["spend_usd"] == 0.40


def test_reconcile_zero_actual_unwinds_estimate(conn):
    reserve_meme_spend(conn, "op", "tig", estimate=0.15, now=_NOW)
    reconcile_meme_spend(conn, "op", "tig", estimate=0.15, actual=0.0, now=_NOW)
    conn.commit()
    st = operator_meme_status(conn, "op", "tig", now=_NOW)
    assert st["spend_usd"] == 0.0                        # full refund when no call happened


def test_reserve_blocks_at_cap_and_refunds(conn):
    # Drive spend up to the $5 cap (10 x $0.50 actual), then the next reserve must be refused.
    for _ in range(10):
        reserve_meme_spend(conn, "op", "tig", estimate=0.50, now=_NOW)
        reconcile_meme_spend(conn, "op", "tig", estimate=0.50, actual=0.50, now=_NOW)
    conn.commit()
    at_cap = operator_meme_status(conn, "op", "tig", now=_NOW)
    assert at_cap["spend_usd"] == 5.0 and at_cap["allowed"] is False

    blocked = reserve_meme_spend(conn, "op", "tig", estimate=0.15, now=_NOW)
    conn.commit()
    assert blocked["allowed"] is False and blocked["estimate"] == 0.0
    after = operator_meme_status(conn, "op", "tig", now=_NOW)
    assert after["spend_usd"] == 5.0 and after["runs"] == 10   # refund left spend+runs untouched


def test_reserve_that_would_cross_cap_is_refused(conn):
    conn.execute("UPDATE orgs SET config_json = ? WHERE org_id = ?",
                 (json.dumps({"max_meme_usd_per_operator_per_week": 1.0}), "tig"))
    conn.commit()
    # spend 0.95, then a 0.15 estimate would cross 1.0 -> refused, spend stays 0.95
    reserve_meme_spend(conn, "op", "tig", estimate=0.95, now=_NOW)
    reconcile_meme_spend(conn, "op", "tig", estimate=0.95, actual=0.95, now=_NOW)
    conn.commit()
    blocked = reserve_meme_spend(conn, "op", "tig", estimate=0.15, now=_NOW)
    conn.commit()
    assert blocked["allowed"] is False
    assert operator_meme_status(conn, "op", "tig", now=_NOW)["spend_usd"] == 0.95


def test_per_operator_and_per_org_isolation(conn):
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES (?, ?, ?)",
                 ("psy", "PSY", "active"))
    conn.commit()
    reserve_meme_spend(conn, "operator_sieg", "tig", estimate=0.15, now=_NOW)
    reconcile_meme_spend(conn, "operator_sieg", "tig", estimate=0.15, actual=0.10, now=_NOW)
    conn.commit()
    # a different operator on the same org is untouched
    assert operator_meme_status(conn, "operator_arf", "tig", now=_NOW)["spend_usd"] == 0.0
    # the same operator on a different org is untouched
    assert operator_meme_status(conn, "operator_sieg", "psy", now=_NOW)["spend_usd"] == 0.0
    assert operator_meme_status(conn, "operator_sieg", "tig", now=_NOW)["spend_usd"] == 0.10


def test_week_rollover_resets_budget(conn):
    for _ in range(10):
        reserve_meme_spend(conn, "op", "tig", estimate=0.50, now=_NOW)
        reconcile_meme_spend(conn, "op", "tig", estimate=0.50, actual=0.50, now=_NOW)
    conn.commit()
    assert operator_meme_status(conn, "op", "tig", now=_NOW)["allowed"] is False
    # next ISO week -> fresh $5
    nxt = operator_meme_status(conn, "op", "tig", now=_NEXT_WEEK)
    assert nxt["spend_usd"] == 0.0 and nxt["allowed"] is True and nxt["remaining_usd"] == 5.0
