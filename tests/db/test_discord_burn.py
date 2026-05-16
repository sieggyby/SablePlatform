"""Tests for discord_burn DB helpers (sable-roles V2 burn-me)."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from sable_platform.db.compat_conn import CompatConnection
from sable_platform.db.discord_burn import (
    VALID_BURN_MODES,
    consume_optin_if_present,
    count_roasts_today,
    get_optin,
    log_random_roast,
    opt_in,
    opt_out,
    was_recently_random_roasted,
)


# ---------------------------------------------------------------------------
# opt_in / opt_out / get_optin
# ---------------------------------------------------------------------------


def test_opt_in_inserts_row(in_memory_db):
    opt_in(in_memory_db, "guild_1", "user_1", "once", opted_in_by="user_1")
    row = get_optin(in_memory_db, "guild_1", "user_1")
    assert row is not None
    assert row["guild_id"] == "guild_1"
    assert row["user_id"] == "user_1"
    assert row["mode"] == "once"
    assert row["opted_in_by"] == "user_1"
    assert row["opted_in_at"] is not None


def test_opt_in_upserts_on_conflict(in_memory_db):
    opt_in(in_memory_db, "guild_1", "user_1", "once", opted_in_by="user_1")
    # Re-opt with persist mode + mod-initiated opt-in: both fields must update.
    opt_in(in_memory_db, "guild_1", "user_1", "persist", opted_in_by="mod_a")
    row = get_optin(in_memory_db, "guild_1", "user_1")
    assert row["mode"] == "persist"
    assert row["opted_in_by"] == "mod_a"


def test_opt_in_rejects_invalid_mode(in_memory_db):
    with pytest.raises(ValueError, match="must be one of"):
        opt_in(in_memory_db, "guild_1", "user_1", "always", opted_in_by="user_1")


def test_opt_in_accepts_all_valid_modes(in_memory_db):
    for mode in VALID_BURN_MODES:
        opt_in(in_memory_db, "guild_1", "user_1", mode, opted_in_by="user_1")
        row = get_optin(in_memory_db, "guild_1", "user_1")
        assert row["mode"] == mode


def test_opt_out_returns_true_when_row_existed(in_memory_db):
    opt_in(in_memory_db, "guild_1", "user_1", "persist", opted_in_by="user_1")
    assert opt_out(in_memory_db, "guild_1", "user_1") is True
    assert get_optin(in_memory_db, "guild_1", "user_1") is None


def test_opt_out_returns_false_when_no_row(in_memory_db):
    assert opt_out(in_memory_db, "guild_1", "ghost_user") is False


def test_get_optin_returns_none_for_unknown_user(in_memory_db):
    assert get_optin(in_memory_db, "guild_1", "ghost") is None


# ---------------------------------------------------------------------------
# consume_optin_if_present
# ---------------------------------------------------------------------------


def test_consume_optin_once_returns_mode_and_deletes(in_memory_db):
    opt_in(in_memory_db, "guild_1", "user_1", "once", opted_in_by="user_1")
    mode = consume_optin_if_present(in_memory_db, "guild_1", "user_1")
    assert mode == "once"
    # Row removed on once-mode consume
    assert get_optin(in_memory_db, "guild_1", "user_1") is None


def test_consume_optin_persist_returns_mode_and_keeps_row(in_memory_db):
    opt_in(in_memory_db, "guild_1", "user_1", "persist", opted_in_by="user_1")
    mode = consume_optin_if_present(in_memory_db, "guild_1", "user_1")
    assert mode == "persist"
    # Row preserved — subsequent images keep getting roasted
    assert get_optin(in_memory_db, "guild_1", "user_1") is not None


def test_consume_optin_returns_none_when_no_optin(in_memory_db):
    assert consume_optin_if_present(in_memory_db, "guild_1", "user_1") is None


# ---------------------------------------------------------------------------
# random log + 7d dedup
# ---------------------------------------------------------------------------


def test_log_random_roast_inserts_row(in_memory_db):
    log_random_roast(in_memory_db, "guild_1", "user_1")
    row = in_memory_db.execute(
        "SELECT guild_id, user_id, roasted_at FROM discord_burn_random_log"
    ).fetchone()
    assert row["guild_id"] == "guild_1"
    assert row["user_id"] == "user_1"
    assert row["roasted_at"] is not None


def test_was_recently_random_roasted_false_when_no_rows(in_memory_db):
    assert was_recently_random_roasted(in_memory_db, "guild_1", "user_1") is False


def test_was_recently_random_roasted_true_within_window(in_memory_db):
    log_random_roast(in_memory_db, "guild_1", "user_1")
    assert was_recently_random_roasted(in_memory_db, "guild_1", "user_1", within_days=7) is True


def test_was_recently_random_roasted_false_outside_window(in_memory_db):
    # Backdate a random-log row to 10 days ago.
    old = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    in_memory_db.execute(
        text(
            "INSERT INTO discord_burn_random_log (guild_id, user_id, roasted_at)"
            " VALUES (:g, :u, :r)"
        ),
        {"g": "guild_1", "u": "user_1", "r": old},
    )
    in_memory_db.commit()
    assert was_recently_random_roasted(in_memory_db, "guild_1", "user_1", within_days=7) is False


def test_random_log_isolated_per_user(in_memory_db):
    log_random_roast(in_memory_db, "guild_1", "user_a")
    assert was_recently_random_roasted(in_memory_db, "guild_1", "user_a") is True
    assert was_recently_random_roasted(in_memory_db, "guild_1", "user_b") is False


def test_random_log_isolated_per_guild(in_memory_db):
    log_random_roast(in_memory_db, "guild_a", "user_1")
    assert was_recently_random_roasted(in_memory_db, "guild_a", "user_1") is True
    assert was_recently_random_roasted(in_memory_db, "guild_b", "user_1") is False


# ---------------------------------------------------------------------------
# count_roasts_today
# ---------------------------------------------------------------------------


def _insert_audit_roast(
    conn,
    *,
    guild_id: str,
    user_id: str,
    action: str = "fitcheck_roast_generated",
    timestamp: str | None = None,
    source: str = "sable-roles",
) -> None:
    detail = json.dumps({"guild_id": guild_id, "user_id": user_id})
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        text(
            "INSERT INTO audit_log (actor, action, org_id, detail_json, source, timestamp)"
            " VALUES (:actor, :action, :org_id, :detail, :source, :ts)"
        ),
        {
            "actor": "discord:bot:auto",
            "action": action,
            "org_id": "test_org_001",
            "detail": detail,
            "source": source,
            "ts": ts,
        },
    )
    conn.commit()


def test_count_roasts_today_zero_when_no_rows(in_memory_db):
    assert count_roasts_today(in_memory_db, "guild_1", "user_1") == 0


def test_count_roasts_today_counts_generated_only(in_memory_db):
    _insert_audit_roast(in_memory_db, guild_id="guild_1", user_id="user_1")
    _insert_audit_roast(in_memory_db, guild_id="guild_1", user_id="user_1")
    # A skipped row should NOT count toward the cap.
    _insert_audit_roast(
        in_memory_db, guild_id="guild_1", user_id="user_1", action="fitcheck_roast_skipped"
    )
    assert count_roasts_today(in_memory_db, "guild_1", "user_1") == 2


def test_count_roasts_today_ignores_other_sources(in_memory_db):
    # Same action, same user, but source != 'sable-roles' must be ignored.
    _insert_audit_roast(
        in_memory_db, guild_id="guild_1", user_id="user_1", source="cli"
    )
    _insert_audit_roast(in_memory_db, guild_id="guild_1", user_id="user_1")
    assert count_roasts_today(in_memory_db, "guild_1", "user_1") == 1


def test_count_roasts_today_isolated_per_user_and_guild(in_memory_db):
    _insert_audit_roast(in_memory_db, guild_id="guild_1", user_id="user_a")
    _insert_audit_roast(in_memory_db, guild_id="guild_1", user_id="user_b")
    _insert_audit_roast(in_memory_db, guild_id="guild_2", user_id="user_a")
    assert count_roasts_today(in_memory_db, "guild_1", "user_a") == 1
    assert count_roasts_today(in_memory_db, "guild_1", "user_b") == 1
    assert count_roasts_today(in_memory_db, "guild_2", "user_a") == 1
    assert count_roasts_today(in_memory_db, "guild_2", "user_b") == 0


def test_count_roasts_today_respects_utc_day_boundary(in_memory_db):
    # A roast from "yesterday" must not count toward today's cap.
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    _insert_audit_roast(
        in_memory_db, guild_id="guild_1", user_id="user_1", timestamp=yesterday
    )
    _insert_audit_roast(in_memory_db, guild_id="guild_1", user_id="user_1")
    assert count_roasts_today(in_memory_db, "guild_1", "user_1") == 1


def test_count_roasts_today_emits_correct_dialect_sql(in_memory_db):
    """R0 sanity check: the helpers are wired through and produce SQLite syntax.

    The Postgres branch is exercised when SABLE_TEST_POSTGRES_URL is set (see
    test_count_roasts_today_works_on_postgres) — this guards the SQLite path
    from a regression where someone hardcodes the Postgres syntax back in.
    """
    from sable_platform.db.compat import get_dialect

    assert get_dialect(in_memory_db) == "sqlite"
    # Round-trip a sample to confirm the dialect-aware query still runs without
    # raising (SQLite would error on `(col::jsonb)->>'k'` if that leaked in).
    _insert_audit_roast(in_memory_db, guild_id="guild_x", user_id="user_x")
    assert count_roasts_today(in_memory_db, "guild_x", "user_x") == 1


def test_count_roasts_today_postgres_sql_compiles_with_bindparams(monkeypatch):
    """Regression guard for R0 QA finding BLOCKER #1: `:today::date` in a
    SQLAlchemy `text()` fragment binds incorrectly — `::` is greedy in
    bindparam-name parsing and splits `:today` into `:toda` + literal `:date`.
    The fix is `CAST(:today AS DATE)`. This test compiles the emitted SQL
    against the Postgres dialect and asserts the bindparam set is intact —
    runs without needing a live Postgres connection.
    """
    from sqlalchemy.dialects import postgresql

    import sable_platform.db.discord_burn as db_burn

    captured: dict = {}

    class _FakeConn:
        def execute(self, t, params):
            captured["text"] = t
            captured["params"] = params

            class _R:
                def fetchone(self_inner):
                    return None

            return _R()

    monkeypatch.setattr(db_burn, "get_dialect", lambda _conn: "postgresql")
    db_burn.count_roasts_today(_FakeConn(), "g", "u")
    sql_obj = captured["text"]

    # Compile against the Postgres dialect — bindparam names must include
    # 'today'. If the greedy-`::` regression returns, 'today' is replaced by
    # 'toda' and this assertion fails loudly.
    compiled = sql_obj.compile(dialect=postgresql.dialect())
    bindparam_names = set(compiled.params.keys())
    assert "today" in bindparam_names, (
        f"`:today` bindparam missing — likely greedy ::-cast regression. "
        f"Bindparams compiled: {bindparam_names}"
    )
    sql_str = str(compiled)
    assert "CAST" in sql_str and "AS DATE" in sql_str
    assert ":today::date" not in sql_str
    assert "::jsonb" in sql_str  # confirms the Postgres json branch is active


# ---------------------------------------------------------------------------
# Multi-guild + multi-user isolation on opt-in CRUD
# ---------------------------------------------------------------------------


def test_opt_in_multi_guild_isolation(in_memory_db):
    opt_in(in_memory_db, "guild_a", "user_1", "once", opted_in_by="user_1")
    opt_in(in_memory_db, "guild_b", "user_1", "persist", opted_in_by="user_1")
    a = get_optin(in_memory_db, "guild_a", "user_1")
    b = get_optin(in_memory_db, "guild_b", "user_1")
    assert a["mode"] == "once"
    assert b["mode"] == "persist"


def test_opt_in_multi_user_isolation(in_memory_db):
    opt_in(in_memory_db, "guild_1", "user_a", "once", opted_in_by="user_a")
    opt_in(in_memory_db, "guild_1", "user_b", "persist", opted_in_by="user_b")
    assert get_optin(in_memory_db, "guild_1", "user_a")["mode"] == "once"
    assert get_optin(in_memory_db, "guild_1", "user_b")["mode"] == "persist"
    # Removing user_a does not affect user_b
    opt_out(in_memory_db, "guild_1", "user_a")
    assert get_optin(in_memory_db, "guild_1", "user_a") is None
    assert get_optin(in_memory_db, "guild_1", "user_b") is not None


# ---------------------------------------------------------------------------
# Postgres parity for count_roasts_today (R0 of the /roast plan)
# ---------------------------------------------------------------------------
# Skipped unless SABLE_TEST_POSTGRES_URL is set. Exercises the
# jsonb->>'key' + ::timestamp::date branch added in compat.py R0.


@pytest.mark.skipif(
    not os.environ.get("SABLE_TEST_POSTGRES_URL"),
    reason="SABLE_TEST_POSTGRES_URL not set",
)
def test_count_roasts_today_works_on_postgres():
    """The Postgres branch of count_roasts_today must run without UndefinedFunction."""
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", os.environ["SABLE_TEST_POSTGRES_URL"])
    command.upgrade(cfg, "head")

    engine = create_engine(os.environ["SABLE_TEST_POSTGRES_URL"])
    sa_conn = engine.connect()
    conn = CompatConnection(sa_conn)
    try:
        guild_id = f"pg_g_{uuid.uuid4().hex[:8]}"
        user_id = f"pg_u_{uuid.uuid4().hex[:8]}"

        # Insert one matching audit row for today and one for yesterday.
        today_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        yest_ts = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).strftime("%Y-%m-%dT%H:%M:%S")
        detail = json.dumps({"guild_id": guild_id, "user_id": user_id})
        for ts in (today_ts, yest_ts):
            conn.execute(
                "INSERT INTO audit_log (actor, action, org_id, detail_json, source, timestamp)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "discord:bot:auto",
                    "fitcheck_roast_generated",
                    None,
                    detail,
                    "sable-roles",
                    ts,
                ),
            )
        conn.commit()

        # Only the today row counts.
        n = count_roasts_today(conn, guild_id, user_id)
        assert n == 1

        # Cleanup so the shared PG instance doesn't accumulate.
        conn.execute(
            "DELETE FROM audit_log WHERE action = ? AND source = ?",
            ("fitcheck_roast_generated", "sable-roles"),
        )
        conn.commit()
    finally:
        conn.close()
        engine.dispose()
