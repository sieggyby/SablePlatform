"""Tests for discord_state_pins (state-pin surface, migration 054)."""
from __future__ import annotations

import time

from sqlalchemy import text

from sable_platform.db.discord_state_pins import (
    get_state_pin,
    upsert_state_pin,
)


# ---------------------------------------------------------------------------
# get_state_pin
# ---------------------------------------------------------------------------


def test_get_state_pin_returns_none_for_missing(in_memory_db):
    assert get_state_pin(in_memory_db, "guild_x", "scoring") is None


def test_get_state_pin_returns_pointer_dict(in_memory_db):
    applied = upsert_state_pin(
        in_memory_db,
        "guild_1",
        "scoring",
        "channel_42",
        "msg_100",
        "2026-05-17T20:00:00Z",
    )
    assert applied is True
    pin = get_state_pin(in_memory_db, "guild_1", "scoring")
    assert pin is not None
    assert pin["channel_id"] == "channel_42"
    assert pin["message_id"] == "msg_100"
    assert pin["posted_at"] == "2026-05-17T20:00:00Z"
    assert "updated_at" in pin and pin["updated_at"]


def test_get_state_pin_scopes_by_guild_and_characteristic(in_memory_db):
    """Same characteristic in different guilds + same guild different
    characteristics are independent rows."""
    upsert_state_pin(in_memory_db, "guild_a", "scoring", "c1", "m1", "2026-05-17T20:00:00Z")
    upsert_state_pin(in_memory_db, "guild_a", "burn_mode", "c1", "m2", "2026-05-17T20:00:00Z")
    upsert_state_pin(in_memory_db, "guild_b", "scoring", "c2", "m3", "2026-05-17T20:00:00Z")
    a_scoring = get_state_pin(in_memory_db, "guild_a", "scoring")
    a_burn = get_state_pin(in_memory_db, "guild_a", "burn_mode")
    b_scoring = get_state_pin(in_memory_db, "guild_b", "scoring")
    assert a_scoring["message_id"] == "m1"
    assert a_burn["message_id"] == "m2"
    assert b_scoring["message_id"] == "m3"


# ---------------------------------------------------------------------------
# upsert_state_pin — insert path
# ---------------------------------------------------------------------------


def test_upsert_state_pin_inserts_when_no_row_exists(in_memory_db):
    """First-ever pin (expected_updated_at=None) lands as a fresh INSERT."""
    applied = upsert_state_pin(
        in_memory_db,
        "guild_1",
        "scoring",
        "channel_42",
        "msg_100",
        "2026-05-17T20:00:00Z",
    )
    assert applied is True
    row = in_memory_db.execute(
        text("SELECT COUNT(*) AS n FROM discord_state_pins")
    ).fetchone()
    assert row[0] == 1


def test_upsert_state_pin_overwrites_when_no_lock(in_memory_db):
    """expected_updated_at=None on an existing row overwrites freely
    (matches the first-ever-pin semantics)."""
    upsert_state_pin(in_memory_db, "g", "scoring", "c1", "m1", "2026-05-17T20:00:00Z")
    applied = upsert_state_pin(
        in_memory_db, "g", "scoring", "c2", "m2", "2026-05-17T21:00:00Z",
    )
    assert applied is True
    pin = get_state_pin(in_memory_db, "g", "scoring")
    assert pin["channel_id"] == "c2"
    assert pin["message_id"] == "m2"


# ---------------------------------------------------------------------------
# upsert_state_pin — optimistic-lock path
# ---------------------------------------------------------------------------


def test_upsert_state_pin_optimistic_lock_applies_when_token_matches(in_memory_db):
    upsert_state_pin(in_memory_db, "g", "scoring", "c1", "m1", "2026-05-17T20:00:00Z")
    prior = get_state_pin(in_memory_db, "g", "scoring")
    # Sleep to guarantee a different updated_at second-resolution after replace.
    time.sleep(1.05)
    applied = upsert_state_pin(
        in_memory_db, "g", "scoring", "c1", "m2", "2026-05-17T21:00:00Z",
        expected_updated_at=prior["updated_at"],
    )
    assert applied is True
    pin = get_state_pin(in_memory_db, "g", "scoring")
    assert pin["message_id"] == "m2"
    assert pin["updated_at"] != prior["updated_at"]


def test_upsert_state_pin_optimistic_lock_rejects_stale_token(in_memory_db):
    """When another writer has bumped updated_at since the caller read prior,
    the optimistic-lock UPDATE matches no rows and returns False."""
    upsert_state_pin(in_memory_db, "g", "scoring", "c1", "m1", "2026-05-17T20:00:00Z")
    prior = get_state_pin(in_memory_db, "g", "scoring")
    # Simulate another writer bumping updated_at by overwriting once with
    # expected_updated_at=None (lock-free overwrite path).
    time.sleep(1.05)
    upsert_state_pin(in_memory_db, "g", "scoring", "c1", "m2", "2026-05-17T20:30:00Z")
    # Now the original caller's expected_updated_at is stale.
    time.sleep(1.05)
    applied = upsert_state_pin(
        in_memory_db, "g", "scoring", "c1", "m3", "2026-05-17T21:00:00Z",
        expected_updated_at=prior["updated_at"],
    )
    assert applied is False
    # Row still reflects the winning writer (m2), not the loser (m3).
    pin = get_state_pin(in_memory_db, "g", "scoring")
    assert pin["message_id"] == "m2"


def test_upsert_state_pin_returns_false_when_no_row_to_lock_against(in_memory_db):
    """Optimistic-lock UPDATE against a non-existent (guild, characteristic)
    matches 0 rows and returns False (caller should retry with
    expected_updated_at=None for the first-ever pin path)."""
    applied = upsert_state_pin(
        in_memory_db, "g", "scoring", "c1", "m1", "2026-05-17T20:00:00Z",
        expected_updated_at="2026-05-17T00:00:00Z",
    )
    assert applied is False
    assert get_state_pin(in_memory_db, "g", "scoring") is None


# ---------------------------------------------------------------------------
# Schema parity (mig 054)
# ---------------------------------------------------------------------------


def test_discord_state_pins_table_columns_present(in_memory_db):
    cols = {
        r["name"]
        for r in in_memory_db.execute(
            text("PRAGMA table_info(discord_state_pins)")
        ).fetchall()
    }
    for expected in (
        "id", "guild_id", "characteristic", "channel_id",
        "message_id", "posted_at", "created_at", "updated_at",
    ):
        assert expected in cols, f"discord_state_pins missing column {expected!r}"


def test_discord_state_pins_unique_constraint_enforced(in_memory_db):
    """UNIQUE (guild_id, characteristic) is the schema-level guard against
    a duplicate-pointer accident. The INSERT path of upsert_state_pin
    uses ON CONFLICT to no-op-replace; a bare INSERT must fail."""
    upsert_state_pin(in_memory_db, "g", "scoring", "c1", "m1", "2026-05-17T20:00:00Z")
    import pytest
    with pytest.raises(Exception):
        in_memory_db.execute(
            text(
                "INSERT INTO discord_state_pins"
                " (guild_id, characteristic, channel_id, message_id,"
                "  posted_at, updated_at)"
                " VALUES ('g', 'scoring', 'cx', 'mx', '2026-05-17T22:00:00Z',"
                "  '2026-05-17T22:00:00Z')"
            )
        )
        in_memory_db.commit()


def test_discord_state_pins_guild_index_exists(in_memory_db):
    rows = in_memory_db.execute(
        text("PRAGMA index_list(discord_state_pins)")
    ).fetchall()
    names = {row[1] for row in rows}
    assert "idx_discord_state_pins_guild" in names
