"""Tests for operator work-tracking helpers (migration 059, SW-TASKING Phase 1)."""
from __future__ import annotations

import json
import uuid

import pytest

from sable_platform.db.connection import get_db
from sable_platform.db.replies import count_replies_delivered
from sable_platform.db.work_tracking import (
    close_mod_slot,
    get_work_summary,
    list_active_slots,
    list_sessions,
    log_work_event,
    open_mod_slot,
)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "sable.db"
    c = get_db(db_path=str(db_path))
    c.execute(
        "INSERT INTO orgs (org_id, display_name, status) VALUES (?, ?, ?)",
        ("tig", "TIG", "active"),
    )
    c.commit()
    yield c
    c.close()


def _add_reply(conn, org_id, posted_at="2026-05-10T12:00:00Z"):
    """Insert a reply_suggestions + reply_outcomes pair (a delivered reply)."""
    sid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO reply_suggestions (id, operator_handle, org_id, source_tweet_id)"
        " VALUES (?, ?, ?, ?)",
        (sid, "@op", org_id, "t1"),
    )
    conn.execute(
        "INSERT INTO reply_outcomes (id, suggestion_id, posted_tweet_id, posted_at)"
        " VALUES (?, ?, ?, ?)",
        (uuid.uuid4().hex, sid, uuid.uuid4().hex, posted_at),
    )


# ---- mod-slot sessions ----------------------------------------------------

def test_open_slot_creates_active_session(conn):
    sid = open_mod_slot(conn, "tig", "@op", ["chat_a", "chat_b"], note="watching")
    conn.commit()
    assert isinstance(sid, str) and sid
    active = list_active_slots(conn, "tig")
    assert len(active) == 1
    assert active[0]["operator_handle"] == "@op"
    assert active[0]["chats_watched"] == ["chat_a", "chat_b"]
    assert active[0]["note"] == "watching"


def test_open_slot_closes_prior_open_slot_for_same_operator(conn):
    open_mod_slot(conn, "tig", "@op", ["chat_a"])
    open_mod_slot(conn, "tig", "@op", ["chat_b"])
    conn.commit()
    active = list_active_slots(conn, "tig")
    assert len(active) == 1  # only the second remains open
    assert active[0]["chats_watched"] == ["chat_b"]


def test_close_slot_returns_true_then_false(conn):
    open_mod_slot(conn, "tig", "@op", ["chat_a"])
    conn.commit()
    assert close_mod_slot(conn, "@op") is True
    conn.commit()
    assert close_mod_slot(conn, "@op") is False  # nothing open now
    assert list_active_slots(conn, "tig") == []


def test_list_sessions_window(conn):
    sid = open_mod_slot(conn, "tig", "@op", ["chat_a"])
    close_mod_slot(conn, "@op", ended_at="2026-05-10T13:00:00Z")
    conn.commit()
    # Force a known started_at so the window assertion is deterministic.
    conn.execute(
        "UPDATE mod_slot_sessions SET started_at = ? WHERE session_id = ?",
        ("2026-05-10T12:00:00Z", sid),
    )
    conn.commit()
    assert len(list_sessions(conn, "tig", since="2026-05-10T00:00:00Z", until="2026-05-11T00:00:00Z")) == 1
    assert list_sessions(conn, "tig", since="2026-05-11T00:00:00Z") == []


# ---- work events ----------------------------------------------------------

def test_log_work_event(conn):
    eid = log_work_event(conn, "tig", "@op", "mod_action", ref={"x": 1})
    conn.commit()
    assert isinstance(eid, str) and eid


# ---- reply counter --------------------------------------------------------

def test_count_replies_delivered_counts_posts(conn):
    _add_reply(conn, "tig")
    _add_reply(conn, "tig")
    conn.commit()
    assert count_replies_delivered(conn, "tig") == 2
    assert count_replies_delivered(conn, "other") == 0


def test_count_replies_delivered_windowed(conn):
    _add_reply(conn, "tig", posted_at="2026-05-10T12:00:00Z")
    _add_reply(conn, "tig", posted_at="2026-05-20T12:00:00Z")
    conn.commit()
    assert count_replies_delivered(conn, "tig", since="2026-05-01T00:00:00Z", until="2026-05-15T00:00:00Z") == 1


def test_count_replies_delivered_null_posted_at_uses_recorded_at(conn):
    # A delivered outcome with NULL posted_at must still count (COALESCE to recorded_at).
    sid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO reply_suggestions (id, operator_handle, org_id, source_tweet_id)"
        " VALUES (?, ?, ?, ?)",
        (sid, "@op", "tig", "t1"),
    )
    conn.execute(
        "INSERT INTO reply_outcomes (id, suggestion_id, posted_tweet_id, posted_at, recorded_at)"
        " VALUES (?, ?, ?, NULL, ?)",
        (uuid.uuid4().hex, sid, uuid.uuid4().hex, "2026-05-10T12:00:00Z"),
    )
    conn.commit()
    assert count_replies_delivered(conn, "tig", since="2026-05-01T00:00:00Z", until="2026-05-15T00:00:00Z") == 1


# ---- the rollup -----------------------------------------------------------

def test_get_work_summary_empty_window_is_zeros(conn):
    s = get_work_summary(conn, "tig")
    assert s["replies_delivered"] == 0
    assert s["declared_coverage_hours"] == 0.0
    assert s["communities_covered"] == 0
    assert s["active_now"] == 0
    assert s["per_operator"] == []
    assert "generated_at" in s


def test_get_work_summary_rolls_up_all_signals(conn):
    # one delivered reply
    _add_reply(conn, "tig", posted_at="2026-05-10T12:30:00Z")
    # one closed 1h session over two communities
    sid = open_mod_slot(conn, "tig", "@op", ["chat_a", "chat_b"])
    close_mod_slot(conn, "@op", ended_at="2026-05-10T13:00:00Z")
    conn.execute(
        "UPDATE mod_slot_sessions SET started_at = ? WHERE session_id = ?",
        ("2026-05-10T12:00:00Z", sid),
    )
    # one still-open session within the window: counts toward communities +
    # active_now, but NOT hours (only closed sessions contribute hours)
    sid2 = open_mod_slot(conn, "tig", "@op2", ["chat_c"])
    conn.execute(
        "UPDATE mod_slot_sessions SET started_at = ? WHERE session_id = ?",
        ("2026-05-10T14:00:00Z", sid2),
    )
    conn.commit()

    s = get_work_summary(conn, "tig", since="2026-05-10T00:00:00Z", until="2026-05-11T00:00:00Z")
    assert s["replies_delivered"] == 1
    assert s["declared_coverage_hours"] == 1.0  # only the closed session
    assert s["communities_covered"] == 3  # chat_a, chat_b, chat_c
    assert s["active_now"] == 1  # @op2 still open
    handles = {p["operator_handle"] for p in s["per_operator"]}
    assert handles == {"@op", "@op2"}


def test_open_slot_excluded_from_coverage_hours(conn):
    open_mod_slot(conn, "tig", "@op", ["chat_a"])
    conn.commit()
    s = get_work_summary(conn, "tig")
    assert s["declared_coverage_hours"] == 0.0  # open slot contributes no hours
    assert s["active_now"] == 1


def test_close_mod_slot_is_operator_scoped(conn):
    open_mod_slot(conn, "tig", "@op", ["chat_a"])
    open_mod_slot(conn, "tig", "@op2", ["chat_b"])
    conn.commit()
    # re-opening for @op must NOT close @op2's open slot (close-before-open is
    # operator-scoped, not global)
    open_mod_slot(conn, "tig", "@op", ["chat_a2"])
    conn.commit()
    handles = sorted(a["operator_handle"] for a in list_active_slots(conn, "tig"))
    assert handles == ["@op", "@op2"]  # both still open


def test_log_work_event_persists_fields(conn):
    log_work_event(conn, "tig", "@op", "mod_action", ref={"k": "v"})
    conn.commit()
    row = conn.execute(
        "SELECT org_id, operator_handle, event_type, ref_json FROM operator_work_events"
    ).fetchone()
    assert row[0] == "tig"
    assert row[1] == "@op"
    assert row[2] == "mod_action"
    assert json.loads(row[3]) == {"k": "v"}


def test_coverage_hours_sums_multiple_closed_sessions(conn):
    s1 = open_mod_slot(conn, "tig", "@op", ["c"])
    close_mod_slot(conn, "@op", ended_at="2026-05-10T13:00:00Z")  # 1.0h
    conn.execute(
        "UPDATE mod_slot_sessions SET started_at = ? WHERE session_id = ?",
        ("2026-05-10T12:00:00Z", s1),
    )
    s2 = open_mod_slot(conn, "tig", "@op", ["c"])
    close_mod_slot(conn, "@op", ended_at="2026-05-10T16:30:00Z")  # 1.5h
    conn.execute(
        "UPDATE mod_slot_sessions SET started_at = ? WHERE session_id = ?",
        ("2026-05-10T15:00:00Z", s2),
    )
    conn.commit()
    s = get_work_summary(conn, "tig", since="2026-05-10T00:00:00Z", until="2026-05-11T00:00:00Z")
    assert s["declared_coverage_hours"] == 2.5
