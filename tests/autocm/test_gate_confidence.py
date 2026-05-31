"""C3.5a — confidence/autonomy decision gate (DESIGN §4 gate/confidence).

Auto vs HITL over ``autocm_category_state``: never-auto categories, the SAFETY §6
``freeze_until`` freeze, runtime ``hitl`` state, and the per-category confidence
floor all force HITL; only a known, auto-eligible, ``auto``-state, unfrozen
category whose confidence clears the threshold returns AUTO.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from sable_platform.autocm.gate.confidence import (
    AUTO,
    HITL,
    REASON_BELOW_THRESHOLD,
    REASON_FROZEN,
    REASON_HITL_STATE,
    REASON_NEVER_AUTO,
    REASON_UNKNOWN_CATEGORY,
    decide,
    is_frozen,
)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_client(conn, org_id):
    conn.execute(
        text(
            "INSERT INTO autocm_clients (org_id, display_name, autonomy_state, enabled) "
            "VALUES (:o, 'RM', 'hitl', 1)"
        ),
        {"o": org_id},
    )
    return conn.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = :o"), {"o": org_id}
    ).fetchone()[0]


def _seed_state(conn, client_id, category, *, state="hitl", threshold=None, freeze_until=None):
    conn.execute(
        text(
            "INSERT INTO autocm_category_state "
            "(client_id, category, state, confidence_threshold, freeze_until) "
            "VALUES (:c, :cat, :s, :t, :fu)"
        ),
        {
            "c": client_id,
            "cat": category,
            "s": state,
            "t": threshold if threshold is not None else 0.8,
            "fu": freeze_until,
        },
    )


# ---------------------------------------------------------------------------
def test_unknown_category_is_hitl(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    conn.commit()
    v = decide(conn, client_id, "not_a_real_category", 0.99)
    assert v.outcome == HITL
    assert v.reason == REASON_UNKNOWN_CATEGORY


def test_never_auto_category_always_hitl(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    # even with a stray 'auto' row, a tier-3 category can never auto-send.
    _seed_state(conn, client_id, "threat", state="auto", threshold=0.1)
    conn.commit()
    v = decide(conn, client_id, "threat", 1.0)
    assert v.outcome == HITL
    assert v.reason == REASON_NEVER_AUTO
    assert v.auto_eligible is False


def test_hitl_state_category_is_hitl(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_state(conn, client_id, "mechanics", state="hitl", threshold=0.8)
    conn.commit()
    v = decide(conn, client_id, "mechanics", 0.99)
    assert v.outcome == HITL
    assert v.reason == REASON_HITL_STATE


def test_fresh_client_no_row_defaults_to_hitl(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    conn.commit()
    # no autocm_category_state row → default state 'hitl'.
    v = decide(conn, client_id, "mechanics", 0.99)
    assert v.outcome == HITL
    assert v.reason == REASON_HITL_STATE


def test_auto_above_threshold_is_auto(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_state(conn, client_id, "mechanics", state="auto", threshold=0.85)
    conn.commit()
    v = decide(conn, client_id, "mechanics", 0.90)
    assert v.outcome == AUTO
    assert v.is_auto is True
    assert v.threshold == 0.85


def test_auto_below_threshold_is_hitl(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_state(conn, client_id, "mechanics", state="auto", threshold=0.85)
    conn.commit()
    v = decide(conn, client_id, "mechanics", 0.80)  # below 0.85
    assert v.outcome == HITL
    assert v.reason == REASON_BELOW_THRESHOLD


def test_confidence_exactly_at_threshold_is_auto(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    _seed_state(conn, client_id, "mechanics", state="auto", threshold=0.85)
    conn.commit()
    v = decide(conn, client_id, "mechanics", 0.85)  # >= floor passes
    assert v.outcome == AUTO


def test_active_freeze_forces_hitl_even_when_auto(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    future = _iso(now + timedelta(hours=48))  # 48h pure-HITL freeze still active
    _seed_state(
        conn, client_id, "mechanics", state="auto", threshold=0.5, freeze_until=future
    )
    conn.commit()
    v = decide(conn, client_id, "mechanics", 0.99, now=now)
    assert v.outcome == HITL
    assert v.reason == REASON_FROZEN
    assert v.frozen is True


def test_expired_freeze_no_longer_forces_hitl(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    past = _iso(now - timedelta(hours=1))  # freeze elapsed
    _seed_state(
        conn, client_id, "mechanics", state="auto", threshold=0.5, freeze_until=past
    )
    conn.commit()
    v = decide(conn, client_id, "mechanics", 0.99, now=now)
    assert v.outcome == AUTO
    assert v.frozen is False


def test_is_frozen_reads_freeze_until(sa_org):
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    _seed_state(
        conn, client_id, "mechanics", state="auto",
        freeze_until=_iso(now + timedelta(hours=1)),
    )
    _seed_state(conn, client_id, "status", state="auto")  # no freeze
    conn.commit()
    assert is_frozen(conn, client_id, "mechanics", now=now) is True
    assert is_frozen(conn, client_id, "status", now=now) is False
    # a category with no row at all is not frozen.
    assert is_frozen(conn, client_id, "greeting", now=now) is False
