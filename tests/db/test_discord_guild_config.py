"""Tests for discord_guild_config DB helpers."""
from __future__ import annotations

import json

import pytest

from sable_platform.db.discord_guild_config import (
    VALID_BURN_MODES,
    get_config,
    set_burn_mode,
    set_personalize_mode,
    set_relax_mode,
)


def test_get_config_returns_defaults_for_unconfigured_guild(in_memory_db):
    cfg = get_config(in_memory_db, "guild_unknown")
    assert cfg["guild_id"] == "guild_unknown"
    assert cfg["relax_mode_on"] == 0
    assert cfg["current_burn_mode"] == "once"
    assert cfg["personalize_mode_on"] == 0
    assert cfg["updated_at"] is None
    assert cfg["updated_by"] is None


# ---------------------------------------------------------------------------
# set_personalize_mode (migration 047)
# ---------------------------------------------------------------------------


def test_set_personalize_mode_on_creates_row(in_memory_db):
    set_personalize_mode(
        in_memory_db, guild_id="guild_1", on=True, updated_by="mod_a"
    )
    cfg = get_config(in_memory_db, "guild_1")
    assert cfg["personalize_mode_on"] == 1
    assert cfg["relax_mode_on"] == 0  # default preserved
    assert cfg["current_burn_mode"] == "once"  # default preserved
    assert cfg["updated_by"] == "mod_a"


def test_set_personalize_mode_upsert_toggles_value(in_memory_db):
    set_personalize_mode(
        in_memory_db, guild_id="guild_1", on=True, updated_by="mod_a"
    )
    set_personalize_mode(
        in_memory_db, guild_id="guild_1", on=False, updated_by="mod_b"
    )
    cfg = get_config(in_memory_db, "guild_1")
    assert cfg["personalize_mode_on"] == 0
    assert cfg["updated_by"] == "mod_b"


def test_set_personalize_mode_preserves_relax_and_burn(in_memory_db):
    set_relax_mode(in_memory_db, "guild_1", on=True, updated_by="mod_a")
    set_burn_mode(in_memory_db, "guild_1", "persist", updated_by="mod_b")
    set_personalize_mode(
        in_memory_db, guild_id="guild_1", on=True, updated_by="mod_c"
    )
    cfg = get_config(in_memory_db, "guild_1")
    assert cfg["relax_mode_on"] == 1
    assert cfg["current_burn_mode"] == "persist"
    assert cfg["personalize_mode_on"] == 1
    assert cfg["updated_by"] == "mod_c"


def test_set_relax_mode_preserves_personalize_on_conflict(in_memory_db):
    set_personalize_mode(
        in_memory_db, guild_id="guild_1", on=True, updated_by="mod_a"
    )
    set_relax_mode(in_memory_db, "guild_1", on=True, updated_by="mod_b")
    cfg = get_config(in_memory_db, "guild_1")
    assert cfg["personalize_mode_on"] == 1
    assert cfg["relax_mode_on"] == 1


def test_set_burn_mode_preserves_personalize_on_conflict(in_memory_db):
    set_personalize_mode(
        in_memory_db, guild_id="guild_1", on=True, updated_by="mod_a"
    )
    set_burn_mode(in_memory_db, "guild_1", "persist", updated_by="mod_b")
    cfg = get_config(in_memory_db, "guild_1")
    assert cfg["personalize_mode_on"] == 1
    assert cfg["current_burn_mode"] == "persist"


# ---------------------------------------------------------------------------
# R3: set_personalize_mode return shape + audit-inside-helper
# ---------------------------------------------------------------------------


def _personalize_audits(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT actor, action, org_id, entity_id, detail_json, source"
        " FROM audit_log WHERE action='fitcheck_personalize_mode_set'"
        " ORDER BY id ASC"
    ).fetchall()
    return [
        dict(r._mapping if hasattr(r, "_mapping") else r) for r in rows
    ]


def test_set_personalize_mode_returns_row(in_memory_db):
    row = set_personalize_mode(
        in_memory_db, guild_id="guild_R", on=True, updated_by="mod_a"
    )
    assert row["guild_id"] == "guild_R"
    assert row["personalize_mode_on"] == 1
    assert row["relax_mode_on"] == 0
    assert row["current_burn_mode"] == "once"
    assert row["updated_by"] == "mod_a"
    assert row["updated_at"] is not None


def test_set_personalize_mode_writes_audit_row(in_memory_db):
    set_personalize_mode(
        in_memory_db, guild_id="guild_R", on=True, updated_by="mod_a"
    )
    audits = _personalize_audits(in_memory_db)
    assert len(audits) == 1
    assert audits[0]["actor"] == "discord:user:mod_a"
    assert audits[0]["action"] == "fitcheck_personalize_mode_set"
    assert audits[0]["org_id"] is None
    assert audits[0]["entity_id"] is None
    assert audits[0]["source"] == "sable-roles"
    detail = json.loads(audits[0]["detail_json"])
    assert detail == {
        "on": True,
        "guild_id": "guild_R",
        "updated_by": "mod_a",
    }


def test_set_personalize_mode_toggle_writes_one_audit_per_call(in_memory_db):
    set_personalize_mode(
        in_memory_db, guild_id="guild_R", on=True, updated_by="mod_a"
    )
    set_personalize_mode(
        in_memory_db, guild_id="guild_R", on=False, updated_by="mod_b"
    )
    set_personalize_mode(
        in_memory_db, guild_id="guild_R", on=True, updated_by="mod_c"
    )
    audits = _personalize_audits(in_memory_db)
    assert len(audits) == 3
    assert [json.loads(a["detail_json"])["on"] for a in audits] == [
        True, False, True
    ]
    assert [a["actor"] for a in audits] == [
        "discord:user:mod_a",
        "discord:user:mod_b",
        "discord:user:mod_c",
    ]
    cfg = get_config(in_memory_db, "guild_R")
    assert cfg["personalize_mode_on"] == 1
    assert cfg["updated_by"] == "mod_c"


def test_set_personalize_mode_multi_guild_audits_isolated(in_memory_db):
    set_personalize_mode(
        in_memory_db, guild_id="guild_A", on=True, updated_by="mod_a"
    )
    set_personalize_mode(
        in_memory_db, guild_id="guild_B", on=False, updated_by="mod_b"
    )
    audits = _personalize_audits(in_memory_db)
    assert len(audits) == 2
    by_guild = {
        json.loads(a["detail_json"])["guild_id"]: json.loads(a["detail_json"])
        for a in audits
    }
    assert by_guild["guild_A"]["on"] is True
    assert by_guild["guild_A"]["updated_by"] == "mod_a"
    assert by_guild["guild_B"]["on"] is False
    assert by_guild["guild_B"]["updated_by"] == "mod_b"


def test_set_personalize_mode_default_row_first_toggle_on(in_memory_db):
    """Migration 047 set personalize_mode_on DEFAULT 0; a fresh guild's first
    ON toggle must land 1, not skip because the column has a default. The
    UPSERT path runs INSERT (not no-op) when no row exists."""
    cfg_before = get_config(in_memory_db, "fresh_guild")
    assert cfg_before["personalize_mode_on"] == 0  # synthetic default
    row = set_personalize_mode(
        in_memory_db, guild_id="fresh_guild", on=True, updated_by="mod_a"
    )
    assert row["personalize_mode_on"] == 1
    cfg_after = get_config(in_memory_db, "fresh_guild")
    assert cfg_after["personalize_mode_on"] == 1


def test_set_personalize_mode_bool_coercion(in_memory_db):
    """Verify True → column 1 + audit detail True, False → column 0 + audit
    detail False. Production callers (sable_roles roast handler) always pass
    a hard Python bool from `mode_value == "on"`; this test locks the round
    trip for that contract only."""
    set_personalize_mode(
        in_memory_db, guild_id="guild_T", on=True, updated_by="mod_t"
    )
    assert get_config(in_memory_db, "guild_T")["personalize_mode_on"] == 1
    set_personalize_mode(
        in_memory_db, guild_id="guild_T", on=False, updated_by="mod_t"
    )
    assert get_config(in_memory_db, "guild_T")["personalize_mode_on"] == 0
    # Audit detail serializes the canonical bool — JSON literal must be the
    # bool token, not 0/1, so R9 grep can match `"on": true` without coercion.
    audits = _personalize_audits(in_memory_db)
    assert [json.loads(a["detail_json"])["on"] for a in audits] == [
        True, False
    ]


def test_set_personalize_mode_signature_kwargs_only(in_memory_db):
    """guild_id/on/updated_by must be kwargs after the conn positional arg.
    Locks the signature so a future drive-by can't silently swap to
    positional args (which would silently change R3's audit-inside contract
    if a caller passed the wrong order)."""
    with pytest.raises(TypeError):
        set_personalize_mode(in_memory_db, "guild_kw", True, "mod_a")  # type: ignore[misc]


def test_set_relax_mode_on_creates_row(in_memory_db):
    set_relax_mode(in_memory_db, "guild_1", on=True, updated_by="mod_user_1")
    cfg = get_config(in_memory_db, "guild_1")
    assert cfg["relax_mode_on"] == 1
    assert cfg["current_burn_mode"] == "once"  # default preserved on first insert
    assert cfg["updated_by"] == "mod_user_1"
    assert cfg["updated_at"] is not None


def test_set_relax_mode_off_creates_row_too(in_memory_db):
    # Edge case: a mod can toggle relax-mode "off" before it was ever turned on.
    # The insert path still runs and writes 0.
    set_relax_mode(in_memory_db, "guild_1", on=False, updated_by="mod_user_1")
    cfg = get_config(in_memory_db, "guild_1")
    assert cfg["relax_mode_on"] == 0
    assert cfg["updated_by"] == "mod_user_1"


def test_set_relax_mode_upsert_toggles_value(in_memory_db):
    set_relax_mode(in_memory_db, "guild_1", on=True, updated_by="mod_a")
    set_relax_mode(in_memory_db, "guild_1", on=False, updated_by="mod_b")
    cfg = get_config(in_memory_db, "guild_1")
    assert cfg["relax_mode_on"] == 0
    assert cfg["updated_by"] == "mod_b"


def test_set_relax_mode_preserves_burn_mode_on_conflict(in_memory_db):
    set_burn_mode(in_memory_db, "guild_1", "persist", updated_by="mod_a")
    set_relax_mode(in_memory_db, "guild_1", on=True, updated_by="mod_b")
    cfg = get_config(in_memory_db, "guild_1")
    # burn_mode set first; relax flip MUST NOT clobber it
    assert cfg["current_burn_mode"] == "persist"
    assert cfg["relax_mode_on"] == 1
    assert cfg["updated_by"] == "mod_b"


def test_set_burn_mode_persists(in_memory_db):
    set_burn_mode(in_memory_db, "guild_1", "persist", updated_by="mod_a")
    cfg = get_config(in_memory_db, "guild_1")
    assert cfg["current_burn_mode"] == "persist"
    assert cfg["relax_mode_on"] == 0  # default preserved


def test_set_burn_mode_preserves_relax_on_conflict(in_memory_db):
    set_relax_mode(in_memory_db, "guild_1", on=True, updated_by="mod_a")
    set_burn_mode(in_memory_db, "guild_1", "persist", updated_by="mod_b")
    cfg = get_config(in_memory_db, "guild_1")
    # relax set first; burn-mode flip MUST NOT clobber it
    assert cfg["relax_mode_on"] == 1
    assert cfg["current_burn_mode"] == "persist"
    assert cfg["updated_by"] == "mod_b"


def test_set_burn_mode_rejects_invalid(in_memory_db):
    with pytest.raises(ValueError, match="must be one of"):
        set_burn_mode(in_memory_db, "guild_1", "always", updated_by="mod_a")


def test_set_burn_mode_accepts_valid_values(in_memory_db):
    for mode in VALID_BURN_MODES:
        set_burn_mode(in_memory_db, "guild_1", mode, updated_by="mod_a")
        assert get_config(in_memory_db, "guild_1")["current_burn_mode"] == mode


def test_multiple_guilds_isolated(in_memory_db):
    set_relax_mode(in_memory_db, "guild_a", on=True, updated_by="mod_a")
    set_relax_mode(in_memory_db, "guild_b", on=False, updated_by="mod_b")
    set_burn_mode(in_memory_db, "guild_a", "persist", updated_by="mod_a")
    set_burn_mode(in_memory_db, "guild_b", "once", updated_by="mod_b")

    a = get_config(in_memory_db, "guild_a")
    b = get_config(in_memory_db, "guild_b")
    assert a["relax_mode_on"] == 1
    assert a["current_burn_mode"] == "persist"
    assert b["relax_mode_on"] == 0
    assert b["current_burn_mode"] == "once"
