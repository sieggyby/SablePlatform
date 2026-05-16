"""Tests for discord_airlock SP helpers (mig 048 — sable-roles airlock)."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from sable_platform.db.discord_airlock import (
    VALID_AIRLOCK_STATUSES,
    add_team_inviter,
    attribute_join,
    delete_invite_snapshot,
    get_admit,
    get_invite_snapshot,
    is_team_inviter,
    list_pending_airlock,
    list_team_inviters,
    record_member_admit,
    remove_team_inviter,
    set_airlock_status,
    upsert_invite_snapshot,
)


# ---------------------------------------------------------------------------
# Invite snapshot
# ---------------------------------------------------------------------------


def test_invite_snapshot_upsert_insert(in_memory_db):
    upsert_invite_snapshot(
        in_memory_db,
        guild_id="g1", code="abc", inviter_user_id="u1",
        uses=0, max_uses=0, expires_at=None,
    )
    snap = get_invite_snapshot(in_memory_db, "g1")
    assert "abc" in snap
    assert snap["abc"]["inviter_user_id"] == "u1"
    assert snap["abc"]["uses"] == 0


def test_invite_snapshot_upsert_updates_uses(in_memory_db):
    upsert_invite_snapshot(
        in_memory_db,
        guild_id="g1", code="abc", inviter_user_id="u1",
        uses=0, max_uses=0, expires_at=None,
    )
    upsert_invite_snapshot(
        in_memory_db,
        guild_id="g1", code="abc", inviter_user_id="u1",
        uses=5, max_uses=0, expires_at=None,
    )
    snap = get_invite_snapshot(in_memory_db, "g1")
    assert snap["abc"]["uses"] == 5


def test_invite_snapshot_delete(in_memory_db):
    upsert_invite_snapshot(
        in_memory_db,
        guild_id="g1", code="abc", inviter_user_id="u1",
        uses=0, max_uses=0, expires_at=None,
    )
    assert delete_invite_snapshot(in_memory_db, guild_id="g1", code="abc") is True
    assert get_invite_snapshot(in_memory_db, "g1") == {}
    # Second delete is a no-op
    assert delete_invite_snapshot(in_memory_db, guild_id="g1", code="abc") is False


def test_invite_snapshot_isolated_per_guild(in_memory_db):
    upsert_invite_snapshot(
        in_memory_db,
        guild_id="g_a", code="abc", inviter_user_id="u1",
        uses=0, max_uses=0, expires_at=None,
    )
    assert "abc" in get_invite_snapshot(in_memory_db, "g_a")
    assert get_invite_snapshot(in_memory_db, "g_b") == {}


# ---------------------------------------------------------------------------
# attribute_join — the diff logic
# ---------------------------------------------------------------------------


def _seed_snapshot(conn, rows):
    for r in rows:
        upsert_invite_snapshot(
            conn,
            guild_id=r["guild_id"], code=r["code"],
            inviter_user_id=r.get("inviter_user_id"),
            uses=r.get("uses", 0), max_uses=r.get("max_uses", 0),
            expires_at=r.get("expires_at"),
        )


def test_attribute_join_single_increment(in_memory_db):
    """One invite's uses went up → that's the attribution."""
    _seed_snapshot(in_memory_db, [
        {"guild_id": "g1", "code": "abc", "inviter_user_id": "u1", "uses": 5},
        {"guild_id": "g1", "code": "def", "inviter_user_id": "u2", "uses": 3},
    ])
    fresh = [
        {"code": "abc", "inviter_user_id": "u1", "uses": 6,
         "max_uses": 0, "expires_at": None},
        {"code": "def", "inviter_user_id": "u2", "uses": 3,
         "max_uses": 0, "expires_at": None},
    ]
    result = attribute_join(in_memory_db, guild_id="g1", fresh_invites=fresh)
    assert result is not None
    assert result["code"] == "abc"
    assert result["inviter_user_id"] == "u1"


def test_attribute_join_disappeared_max_uses(in_memory_db):
    """An invite with max_uses=1 that got consumed disappears from the live
    fresh_invites list. We should still attribute the join to it."""
    _seed_snapshot(in_memory_db, [
        {"guild_id": "g1", "code": "abc", "inviter_user_id": "u1",
         "uses": 0, "max_uses": 1},
        {"guild_id": "g1", "code": "def", "inviter_user_id": "u2", "uses": 3},
    ])
    fresh = [
        {"code": "def", "inviter_user_id": "u2", "uses": 3,
         "max_uses": 0, "expires_at": None},
    ]  # abc is gone
    result = attribute_join(in_memory_db, guild_id="g1", fresh_invites=fresh)
    assert result is not None
    assert result["code"] == "abc"


def test_attribute_join_ambiguous_multiple_increments(in_memory_db):
    """Two simultaneous joins → both invites' uses incremented → ambiguous."""
    _seed_snapshot(in_memory_db, [
        {"guild_id": "g1", "code": "abc", "uses": 5, "inviter_user_id": "u1"},
        {"guild_id": "g1", "code": "def", "uses": 3, "inviter_user_id": "u2"},
    ])
    fresh = [
        {"code": "abc", "inviter_user_id": "u1", "uses": 6,
         "max_uses": 0, "expires_at": None},
        {"code": "def", "inviter_user_id": "u2", "uses": 4,
         "max_uses": 0, "expires_at": None},
    ]
    assert attribute_join(in_memory_db, guild_id="g1", fresh_invites=fresh) is None


def test_attribute_join_no_change_returns_none(in_memory_db):
    """Vanity-URL join or external attack — no invite changed."""
    _seed_snapshot(in_memory_db, [
        {"guild_id": "g1", "code": "abc", "uses": 5, "inviter_user_id": "u1"},
    ])
    fresh = [
        {"code": "abc", "inviter_user_id": "u1", "uses": 5,
         "max_uses": 0, "expires_at": None},
    ]
    assert attribute_join(in_memory_db, guild_id="g1", fresh_invites=fresh) is None


def test_attribute_join_new_lone_invite_attributes(in_memory_db):
    """A new invite seen for the first time with uses>0, and NO other
    invite changed — unambiguous attribution to that new invite. Covers
    the on_invite_create-missed scenario (bot lacked Manage Server when
    inviter created the invite, then joinee uses it).
    """
    fresh = [
        {"code": "mystery", "inviter_user_id": "u_x", "uses": 1,
         "max_uses": 0, "expires_at": None},
    ]
    result = attribute_join(in_memory_db, guild_id="g1", fresh_invites=fresh)
    assert result is not None
    assert result["code"] == "mystery"
    assert result["inviter_user_id"] == "u_x"


def test_attribute_join_new_invite_with_zero_uses_skipped(in_memory_db):
    """A new invite landed but uses=0 → caller didn't use it; not an
    attribution candidate."""
    fresh = [
        {"code": "freshcreate", "inviter_user_id": "u_x", "uses": 0,
         "max_uses": 0, "expires_at": None},
    ]
    assert attribute_join(in_memory_db, guild_id="g1", fresh_invites=fresh) is None


def test_attribute_join_new_invite_ambiguous_with_increment(in_memory_db):
    """If a new invite shows up AND an existing invite incremented in
    the same window, the diff is ambiguous → fail-closed."""
    _seed_snapshot(in_memory_db, [
        {"guild_id": "g1", "code": "old", "uses": 5, "inviter_user_id": "u1"},
    ])
    fresh = [
        {"code": "old", "inviter_user_id": "u1", "uses": 6,
         "max_uses": 0, "expires_at": None},
        {"code": "new", "inviter_user_id": "u2", "uses": 1,
         "max_uses": 0, "expires_at": None},
    ]
    assert attribute_join(in_memory_db, guild_id="g1", fresh_invites=fresh) is None


def test_attribute_join_disappeared_but_not_max_uses_excluded(in_memory_db):
    """An invite that disappeared but DIDN'T have max_uses hit (e.g. mod
    deleted it) is NOT a legitimate attribution — return None."""
    _seed_snapshot(in_memory_db, [
        {"guild_id": "g1", "code": "abc", "uses": 5, "max_uses": 0,
         "inviter_user_id": "u1"},
    ])
    fresh = []  # mod deleted abc
    assert attribute_join(in_memory_db, guild_id="g1", fresh_invites=fresh) is None


# ---------------------------------------------------------------------------
# Team inviters
# ---------------------------------------------------------------------------


def test_add_team_inviter_returns_true_on_first_add(in_memory_db):
    assert add_team_inviter(
        in_memory_db, guild_id="g1", user_id="u1", added_by="seed"
    ) is True
    assert is_team_inviter(in_memory_db, "g1", "u1") is True


def test_add_team_inviter_idempotent(in_memory_db):
    add_team_inviter(in_memory_db, guild_id="g1", user_id="u1", added_by="seed")
    # Second add returns False (no-op)
    assert add_team_inviter(
        in_memory_db, guild_id="g1", user_id="u1", added_by="seed2"
    ) is False


def test_remove_team_inviter(in_memory_db):
    add_team_inviter(in_memory_db, guild_id="g1", user_id="u1", added_by="s")
    assert remove_team_inviter(in_memory_db, guild_id="g1", user_id="u1") is True
    assert is_team_inviter(in_memory_db, "g1", "u1") is False
    # Second remove is a no-op
    assert remove_team_inviter(in_memory_db, guild_id="g1", user_id="u1") is False


def test_team_inviter_isolated_per_guild(in_memory_db):
    add_team_inviter(in_memory_db, guild_id="g_a", user_id="u1", added_by="s")
    assert is_team_inviter(in_memory_db, "g_a", "u1") is True
    assert is_team_inviter(in_memory_db, "g_b", "u1") is False


def test_list_team_inviters_orders_oldest_first(in_memory_db):
    add_team_inviter(in_memory_db, guild_id="g1", user_id="u_a", added_by="s")
    add_team_inviter(in_memory_db, guild_id="g1", user_id="u_b", added_by="s")
    add_team_inviter(in_memory_db, guild_id="g2", user_id="u_c", added_by="s")
    users = list_team_inviters(in_memory_db, "g1")
    assert [u["user_id"] for u in users] == ["u_a", "u_b"]


# ---------------------------------------------------------------------------
# Member admit ledger
# ---------------------------------------------------------------------------


def test_record_member_admit_held(in_memory_db):
    rid = record_member_admit(
        in_memory_db,
        guild_id="g1", user_id="u1",
        attributed_invite_code=None,
        attributed_inviter_user_id=None,
        is_team_invite=False,
        airlock_status="held",
    )
    assert rid > 0
    row = get_admit(in_memory_db, "g1", "u1")
    assert row is not None
    assert row["airlock_status"] == "held"
    assert row["is_team_invite"] is False
    assert row["decision_by"] is None
    assert row["decision_at"] is None


def test_record_member_admit_auto_admitted_stamps_decision_at(in_memory_db):
    """auto_admitted is a terminal state → decision_at must be set even
    though decision_by stays None (the bot did it, not a mod)."""
    record_member_admit(
        in_memory_db,
        guild_id="g1", user_id="u1",
        attributed_invite_code="abc",
        attributed_inviter_user_id="u_team",
        is_team_invite=True,
        airlock_status="auto_admitted",
    )
    row = get_admit(in_memory_db, "g1", "u1")
    assert row["airlock_status"] == "auto_admitted"
    assert row["is_team_invite"] is True
    assert row["decision_at"] is not None
    assert row["decision_by"] is None


def test_record_member_admit_invalid_status_raises(in_memory_db):
    with pytest.raises(ValueError, match="airlock_status"):
        record_member_admit(
            in_memory_db,
            guild_id="g1", user_id="u1",
            attributed_invite_code=None,
            attributed_inviter_user_id=None,
            is_team_invite=False,
            airlock_status="bogus",
        )


def test_record_member_admit_rejoin_overwrites(in_memory_db):
    """A second join for (guild, user) replaces the prior row's attribution
    and resets decision_* fields (fresh triage opportunity)."""
    record_member_admit(
        in_memory_db,
        guild_id="g1", user_id="u1",
        attributed_invite_code="old",
        attributed_inviter_user_id="u_x",
        is_team_invite=False,
        airlock_status="kicked",
        decision_by="mod_a", decision_reason="suspicious",
    )
    record_member_admit(
        in_memory_db,
        guild_id="g1", user_id="u1",
        attributed_invite_code="new",
        attributed_inviter_user_id="u_y",
        is_team_invite=False,
        airlock_status="held",
    )
    row = get_admit(in_memory_db, "g1", "u1")
    assert row["attributed_invite_code"] == "new"
    assert row["attributed_inviter_user_id"] == "u_y"
    assert row["airlock_status"] == "held"
    assert row["decision_by"] is None
    assert row["decision_reason"] is None
    # Still exactly one row (UNIQUE enforced)
    n = in_memory_db.execute(
        "SELECT COUNT(*) AS n FROM discord_member_admit"
        " WHERE guild_id='g1' AND user_id='u1'"
    ).fetchone()
    assert dict(n._mapping if hasattr(n, "_mapping") else n)["n"] == 1


def test_set_airlock_status_admit_transition(in_memory_db):
    record_member_admit(
        in_memory_db,
        guild_id="g1", user_id="u1",
        attributed_invite_code=None, attributed_inviter_user_id=None,
        is_team_invite=False, airlock_status="held",
    )
    ok = set_airlock_status(
        in_memory_db,
        guild_id="g1", user_id="u1",
        new_status="admitted", decision_by="mod_a",
    )
    assert ok is True
    row = get_admit(in_memory_db, "g1", "u1")
    assert row["airlock_status"] == "admitted"
    assert row["decision_by"] == "mod_a"
    assert row["decision_at"] is not None


def test_set_airlock_status_ban_with_reason(in_memory_db):
    record_member_admit(
        in_memory_db,
        guild_id="g1", user_id="u1",
        attributed_invite_code=None, attributed_inviter_user_id=None,
        is_team_invite=False, airlock_status="held",
    )
    set_airlock_status(
        in_memory_db,
        guild_id="g1", user_id="u1",
        new_status="banned",
        decision_by="mod_a",
        decision_reason="scam profile",
    )
    row = get_admit(in_memory_db, "g1", "u1")
    assert row["airlock_status"] == "banned"
    assert row["decision_reason"] == "scam profile"


def test_set_airlock_status_invalid_status_raises(in_memory_db):
    with pytest.raises(ValueError, match="new_status"):
        set_airlock_status(
            in_memory_db,
            guild_id="g1", user_id="u1",
            new_status="bogus", decision_by="m",
        )


def test_set_airlock_status_returns_false_on_missing_row(in_memory_db):
    """No admit row → set_airlock_status returns False (caller bounces)."""
    ok = set_airlock_status(
        in_memory_db,
        guild_id="g1", user_id="u_nonexistent",
        new_status="admitted", decision_by="mod_a",
    )
    assert ok is False


def test_get_admit_returns_none_when_missing(in_memory_db):
    assert get_admit(in_memory_db, "g1", "u_nonexistent") is None


def test_list_pending_airlock_filters_to_held(in_memory_db):
    """Only 'held' rows appear in the pending list."""
    record_member_admit(
        in_memory_db,
        guild_id="g1", user_id="u_held",
        attributed_invite_code=None, attributed_inviter_user_id=None,
        is_team_invite=False, airlock_status="held",
    )
    record_member_admit(
        in_memory_db,
        guild_id="g1", user_id="u_admitted",
        attributed_invite_code="abc", attributed_inviter_user_id="u_t",
        is_team_invite=True, airlock_status="auto_admitted",
    )
    record_member_admit(
        in_memory_db,
        guild_id="g1", user_id="u_banned",
        attributed_invite_code=None, attributed_inviter_user_id=None,
        is_team_invite=False, airlock_status="held",
    )
    set_airlock_status(
        in_memory_db, guild_id="g1", user_id="u_banned",
        new_status="banned", decision_by="mod_a", decision_reason="spam",
    )
    pending = list_pending_airlock(in_memory_db, "g1")
    assert [r["user_id"] for r in pending] == ["u_held"]


def test_list_pending_airlock_isolated_per_guild(in_memory_db):
    record_member_admit(
        in_memory_db,
        guild_id="g_a", user_id="u1",
        attributed_invite_code=None, attributed_inviter_user_id=None,
        is_team_invite=False, airlock_status="held",
    )
    assert len(list_pending_airlock(in_memory_db, "g_a")) == 1
    assert list_pending_airlock(in_memory_db, "g_b") == []


def test_check_constraint_blocks_invalid_status_via_sql(in_memory_db):
    """Bypass the helper to confirm SQL-level CHECK still fires."""
    with pytest.raises(Exception):
        in_memory_db.execute(
            text(
                "INSERT INTO discord_member_admit"
                " (guild_id, user_id, airlock_status)"
                " VALUES ('g1', 'u1', 'bogus_status')"
            )
        )
        in_memory_db.commit()


def test_valid_airlock_statuses_constant():
    assert set(VALID_AIRLOCK_STATUSES) == {
        "held", "auto_admitted", "admitted", "banned", "kicked",
        "left_during_airlock",
    }
