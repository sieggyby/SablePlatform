"""Tests for discord_scoring_config (Scored Mode V2 Pass B)."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from sable_platform.db.discord_scoring_config import (
    VALID_STATES,
    count_status_breakdown,
    get_config,
    set_state,
)
from sable_platform.db.discord_fitcheck_scores import (
    invalidate_score,
    record_score_failure,
    upsert_score_success,
)


def _success_kwargs(org_id: str, post_id: str) -> dict:
    return {
        "org_id": org_id,
        "guild_id": "guild_1",
        "post_id": post_id,
        "user_id": "user_1",
        "posted_at": "2026-05-10T12:00:00Z",
        "scored_at": "2026-05-10T12:00:05Z",
        "model_id": "claude-sonnet-4-6",
        "prompt_version": "rubric_v1",
        "axis_cohesion": 7,
        "axis_execution": 8,
        "axis_concept": 6,
        "axis_catch": 5,
        "raw_total": 26,
        "catch_detected": None,
        "catch_naming_class": None,
        "description": None,
        "confidence": None,
        "axis_rationales_json": None,
        "curve_basis": "absolute",
        "pool_size_at_score_time": 0,
        "percentile": 65.0,
    }


# ---------------------------------------------------------------------------
# get_config defaults
# ---------------------------------------------------------------------------


def test_get_config_returns_off_default_for_unconfigured_guild(org_db):
    conn, _ = org_db
    cfg = get_config(conn, "guild_X")
    # CRITICAL DEFAULT — first deploy must be 'off'.
    assert cfg["state"] == "off"
    assert cfg["state_changed_by"] is None
    assert cfg["state_changed_at"] is None
    assert cfg["reaction_threshold"] == 10
    assert cfg["thread_message_threshold"] == 100
    assert cfg["reveal_window_days"] == 7
    assert cfg["reveal_min_age_minutes"] == 10
    assert cfg["curve_window_days"] == 30
    assert cfg["cold_start_min_pool"] == 20
    assert cfg["model_id"] == "claude-sonnet-4-6"
    assert cfg["prompt_version"] == "rubric_v1"
    assert cfg["guild_id"] == "guild_X"
    assert cfg["org_id"] is None  # unconfigured -> None


def test_default_state_after_migration_is_off(org_db):
    """Defense in depth: the SQL DEFAULT must be 'off'.

    Insert a bare row (no state column) and verify state defaults to 'off'.
    Verifies the migration's DEFAULT clause, not the helper's behavior.
    """
    conn, org_id = org_db
    conn.execute(
        text(
            "INSERT INTO discord_scoring_config (org_id, guild_id) VALUES (:org, :guild)"
        ),
        {"org": org_id, "guild": "guild_Y"},
    )
    conn.commit()
    cfg = get_config(conn, "guild_Y")
    assert cfg["state"] == "off"


# ---------------------------------------------------------------------------
# set_state — happy path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target_state", ["silent", "revealed", "off"])
def test_set_state_writes_row_and_audit(org_db, target_state):
    conn, org_id = org_db
    result = set_state(
        conn,
        org_id=org_id,
        guild_id="guild_1",
        state=target_state,
        updated_by="555",
    )
    assert result["state"] == target_state
    assert result["state_changed_by"] == "555"
    assert result["state_changed_at"] is not None

    # Audit row present.
    audit = conn.execute(
        text(
            "SELECT actor, action, source, detail_json FROM audit_log"
            " ORDER BY id DESC LIMIT 1"
        )
    ).fetchone()
    assert audit is not None
    assert audit["actor"] == "discord:user:555"
    assert audit["action"] == "fitcheck_scoring_state_changed"
    assert audit["source"] == "sable-roles"
    detail = json.loads(audit["detail_json"])
    assert detail["guild_id"] == "guild_1"
    assert detail["new_state"] == target_state
    # First transition: prior_state should be 'off' (the default).
    assert detail["prior_state"] == "off"


# ---------------------------------------------------------------------------
# set_state — state machine transitions cover all 6 pairings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "from_state,to_state",
    [
        ("off", "silent"),
        ("silent", "revealed"),
        ("revealed", "silent"),
        ("revealed", "off"),
        ("silent", "off"),
        ("off", "revealed"),
    ],
)
def test_state_transitions_all_six_pairings(org_db, from_state, to_state):
    """All transitions must be allowed — design sec 8.1 + 8.3."""
    conn, org_id = org_db
    if from_state != "off":
        set_state(
            conn,
            org_id=org_id,
            guild_id="guild_1",
            state=from_state,
            updated_by="111",
        )

    result = set_state(
        conn,
        org_id=org_id,
        guild_id="guild_1",
        state=to_state,
        updated_by="222",
    )
    assert result["state"] == to_state

    audit = conn.execute(
        text(
            "SELECT detail_json FROM audit_log WHERE action ="
            " 'fitcheck_scoring_state_changed' ORDER BY id DESC LIMIT 1"
        )
    ).fetchone()
    detail = json.loads(audit["detail_json"])
    assert detail["prior_state"] == from_state
    assert detail["new_state"] == to_state


# ---------------------------------------------------------------------------
# set_state — invalid input
# ---------------------------------------------------------------------------


def test_set_state_rejects_invalid_state(org_db):
    conn, org_id = org_db
    with pytest.raises(ValueError):
        set_state(
            conn,
            org_id=org_id,
            guild_id="guild_1",
            state="ENABLED",  # not a valid state
            updated_by="555",
        )


def test_set_state_rejects_arbitrary_typo(org_db):
    conn, org_id = org_db
    with pytest.raises(ValueError):
        set_state(
            conn,
            org_id=org_id,
            guild_id="guild_1",
            state="reveal",  # missing 'ed'
            updated_by="555",
        )


def test_valid_states_constant_is_locked():
    # Lock the only ever-supported set of states.
    assert VALID_STATES == ("off", "silent", "revealed")


# ---------------------------------------------------------------------------
# count_status_breakdown
# ---------------------------------------------------------------------------


def test_count_status_breakdown_aggregates_correctly(org_db):
    conn, org_id = org_db
    for i in range(3):
        upsert_score_success(conn, **_success_kwargs(org_id, f"post_s_{i}"))
    record_score_failure(
        conn, org_id, "guild_1", "post_f_1", "user_1",
        "2026-05-10T12:00:00Z", "2026-05-10T12:00:10Z",
        "claude-sonnet-4-6", "rubric_v1", "transient",
    )
    invalidate_score(conn, "guild_1", "post_s_0", reason="mod")

    breakdown = count_status_breakdown(conn, org_id, "guild_1")
    assert breakdown["success"] == 3
    assert breakdown["failed"] == 1
    assert breakdown["total"] == 4
    assert breakdown["invalidated"] == 1


def test_count_status_breakdown_returns_zeros_for_empty(org_db):
    conn, org_id = org_db
    breakdown = count_status_breakdown(conn, org_id, "guild_1")
    assert breakdown == {"success": 0, "failed": 0, "total": 0, "invalidated": 0}
