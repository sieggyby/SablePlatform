"""Tests for discord_roast DB helpers (sable-roles V2 /roast peer-economy)."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, text

from sable_platform.db.compat_conn import CompatConnection
from sable_platform.db.discord_roast import (
    VALID_TOKEN_SOURCES,
    aggregate_peer_roast_report,
    available_token,
    consume_token,
    cooldown_active_between,
    count_available_tokens,
    count_target_peer_roasts_this_month,
    delete_blocklist,
    find_peer_roast_for_bot_reply,
    grant_monthly_token,
    grant_restoration_token,
    insert_blocklist,
    insert_flag,
    is_blocklisted,
    last_consumed_token,
    list_blocklisted_users,
    list_flags,
    refund_token,
)
from sable_platform.db.schema import metadata as sa_metadata


# ---------------------------------------------------------------------------
# Blocklist
# ---------------------------------------------------------------------------


def test_blocklist_insert_and_check(in_memory_db):
    assert is_blocklisted(in_memory_db, "guild_1", "user_1") is False
    assert insert_blocklist(in_memory_db, "guild_1", "user_1") is True
    assert is_blocklisted(in_memory_db, "guild_1", "user_1") is True


def test_blocklist_insert_is_idempotent(in_memory_db):
    assert insert_blocklist(in_memory_db, "guild_1", "user_1") is True
    assert insert_blocklist(in_memory_db, "guild_1", "user_1") is False
    # Still exactly one row.
    rows = in_memory_db.execute(
        "SELECT COUNT(*) AS n FROM discord_burn_blocklist"
        " WHERE guild_id = 'guild_1' AND user_id = 'user_1'"
    ).fetchone()
    assert rows["n"] == 1


def test_blocklist_delete(in_memory_db):
    insert_blocklist(in_memory_db, "guild_1", "user_1")
    assert delete_blocklist(in_memory_db, "guild_1", "user_1") is True
    assert is_blocklisted(in_memory_db, "guild_1", "user_1") is False
    # Second delete is a no-op.
    assert delete_blocklist(in_memory_db, "guild_1", "user_1") is False


def test_blocklist_multi_guild_isolation(in_memory_db):
    insert_blocklist(in_memory_db, "guild_a", "user_1")
    assert is_blocklisted(in_memory_db, "guild_a", "user_1") is True
    assert is_blocklisted(in_memory_db, "guild_b", "user_1") is False


def test_list_blocklisted_users_orders_oldest_first(in_memory_db):
    insert_blocklist(in_memory_db, "guild_1", "user_a")
    insert_blocklist(in_memory_db, "guild_1", "user_b")
    insert_blocklist(in_memory_db, "guild_2", "user_c")
    users = list_blocklisted_users(in_memory_db, "guild_1")
    assert users == ["user_a", "user_b"]


# ---------------------------------------------------------------------------
# Token grants — uniqueness + race semantics
# ---------------------------------------------------------------------------


def test_grant_monthly_token_first_call_succeeds(in_memory_db):
    assert grant_monthly_token(
        in_memory_db, "guild_1", "user_1", year_month="2026-05"
    ) is True


def test_grant_monthly_token_second_call_returns_false(in_memory_db):
    grant_monthly_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    assert grant_monthly_token(
        in_memory_db, "guild_1", "user_1", year_month="2026-05"
    ) is False


def test_grant_monthly_token_new_month_succeeds(in_memory_db):
    grant_monthly_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    assert grant_monthly_token(
        in_memory_db, "guild_1", "user_1", year_month="2026-06"
    ) is True


def test_monthly_and_restoration_can_coexist_in_same_month(in_memory_db):
    grant_monthly_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    assert grant_restoration_token(
        in_memory_db, "guild_1", "user_1", year_month="2026-05"
    ) is True
    rows = in_memory_db.execute(
        "SELECT source FROM discord_peer_roast_tokens"
        " WHERE guild_id = 'guild_1' AND actor_user_id = 'user_1'"
        " AND year_month = '2026-05' ORDER BY source"
    ).fetchall()
    assert [r["source"] for r in rows] == ["monthly", "streak_restoration"]


def test_grant_invalid_source_raises(in_memory_db):
    from sable_platform.db.discord_roast import _grant_token

    with pytest.raises(ValueError, match="must be one of"):
        _grant_token(in_memory_db, "guild_1", "user_1", "bogus", "2026-05")


def test_concurrent_grant_does_not_double_insert(tmp_path):
    """The UNIQUE(guild_id, actor_user_id, year_month, source) constraint
    must block double-grant under contention.

    This is the post-audit BLOCKER-2 regression guard. Two threads racing
    grant_monthly_token for the same (guild, actor, month) — at most ONE
    must land a row.
    """
    db_path = tmp_path / "race.db"
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ARG001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    sa_metadata.create_all(engine)

    barrier = threading.Barrier(2)
    results: list[bool] = []
    errors: list[BaseException] = []

    def _worker():
        try:
            with engine.connect() as sa_conn:
                conn = CompatConnection(sa_conn)
                barrier.wait(timeout=5)
                granted = grant_monthly_token(
                    conn, "guild_x", "actor_x", year_month="2026-05"
                )
                results.append(granted)
        except BaseException as exc:  # noqa: BLE001 — propagate as test failure
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"worker raised: {errors}"
    assert sorted(results) == [False, True], (
        f"expected exactly one grant + one no-op, got {results}"
    )

    with engine.connect() as sa_conn:
        conn = CompatConnection(sa_conn)
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM discord_peer_roast_tokens"
            " WHERE guild_id = 'guild_x' AND actor_user_id = 'actor_x'"
            " AND year_month = '2026-05' AND source = 'monthly'"
        ).fetchone()["n"]
    engine.dispose()
    assert n == 1, f"UNIQUE constraint must block double-grant, found {n} rows"


def test_valid_token_sources_contains_both(in_memory_db):
    assert set(VALID_TOKEN_SOURCES) == {"monthly", "streak_restoration"}


# ---------------------------------------------------------------------------
# available_token + count_available_tokens
# ---------------------------------------------------------------------------


def test_available_token_returns_none_when_no_grant(in_memory_db):
    assert available_token(
        in_memory_db, "guild_1", "user_1", year_month="2026-05"
    ) is None


def test_available_token_returns_unspent_row(in_memory_db):
    grant_monthly_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    tok = available_token(
        in_memory_db, "guild_1", "user_1", year_month="2026-05"
    )
    assert tok is not None
    assert tok["actor_user_id"] == "user_1"
    assert tok["consumed_at"] is None
    assert tok["source"] == "monthly"


def test_available_token_skips_consumed_row(in_memory_db):
    grant_monthly_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    tok = available_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    consume_token(in_memory_db, tok["id"], target_user_id="t1", post_id="p1")
    assert available_token(
        in_memory_db, "guild_1", "user_1", year_month="2026-05"
    ) is None


def test_count_available_tokens(in_memory_db):
    grant_monthly_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    grant_restoration_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    assert count_available_tokens(
        in_memory_db, "guild_1", "user_1", year_month="2026-05"
    ) == 2
    tok = available_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    consume_token(in_memory_db, tok["id"], target_user_id="t1", post_id="p1")
    assert count_available_tokens(
        in_memory_db, "guild_1", "user_1", year_month="2026-05"
    ) == 1


# ---------------------------------------------------------------------------
# last_consumed_token
# ---------------------------------------------------------------------------


def test_last_consumed_token_returns_none_when_no_tokens(in_memory_db):
    assert last_consumed_token(in_memory_db, "guild_1", "user_1") is None


def test_last_consumed_token_returns_none_when_all_unspent(in_memory_db):
    grant_monthly_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    grant_restoration_token(
        in_memory_db, "guild_1", "user_1", year_month="2026-05"
    )
    assert last_consumed_token(in_memory_db, "guild_1", "user_1") is None


def test_last_consumed_token_returns_only_consumed_row(in_memory_db):
    grant_monthly_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    tok = available_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    consume_token(in_memory_db, tok["id"], target_user_id="t1", post_id="p1")
    last = last_consumed_token(in_memory_db, "guild_1", "user_1")
    assert last is not None
    assert last["id"] == tok["id"]
    assert last["consumed_target_user_id"] == "t1"
    assert last["consumed_on_post_id"] == "p1"
    assert last["consumed_at"] is not None


def test_last_consumed_token_picks_most_recent(in_memory_db):
    """Two tokens consumed 1h apart — the newer must win."""
    grant_monthly_token(in_memory_db, "guild_1", "user_1", year_month="2026-04")
    grant_monthly_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    rows = in_memory_db.execute(
        "SELECT id FROM discord_peer_roast_tokens"
        " WHERE guild_id='guild_1' AND actor_user_id='user_1'"
        " ORDER BY year_month ASC"
    ).fetchall()
    older_id, newer_id = rows[0]["id"], rows[1]["id"]
    consume_token(
        in_memory_db, older_id, target_user_id="t_old", post_id="p_old"
    )
    consume_token(
        in_memory_db, newer_id, target_user_id="t_new", post_id="p_new"
    )
    # Force deterministic ordering — set explicit consumed_at 1h apart.
    in_memory_db.execute(
        text("UPDATE discord_peer_roast_tokens SET consumed_at = :ts WHERE id = :id"),
        {"ts": "2026-05-10T11:00:00Z", "id": older_id},
    )
    in_memory_db.execute(
        text("UPDATE discord_peer_roast_tokens SET consumed_at = :ts WHERE id = :id"),
        {"ts": "2026-05-10T12:00:00Z", "id": newer_id},
    )
    in_memory_db.commit()
    last = last_consumed_token(in_memory_db, "guild_1", "user_1")
    assert last is not None
    assert last["id"] == newer_id
    assert last["consumed_target_user_id"] == "t_new"


def test_last_consumed_token_isolated_per_actor(in_memory_db):
    """A consumed token under a different actor_user_id must not bleed in."""
    grant_monthly_token(in_memory_db, "guild_1", "actor_a", year_month="2026-05")
    grant_monthly_token(in_memory_db, "guild_1", "actor_b", year_month="2026-05")
    tok_a = available_token(
        in_memory_db, "guild_1", "actor_a", year_month="2026-05"
    )
    consume_token(
        in_memory_db, tok_a["id"], target_user_id="t1", post_id="p1"
    )
    # actor_b has not consumed anything.
    assert last_consumed_token(in_memory_db, "guild_1", "actor_b") is None
    # actor_a sees their own consumption.
    last_a = last_consumed_token(in_memory_db, "guild_1", "actor_a")
    assert last_a is not None
    assert last_a["actor_user_id"] == "actor_a"


def test_last_consumed_token_isolated_per_guild(in_memory_db):
    """A consumed token under a different guild_id must not bleed in."""
    grant_monthly_token(in_memory_db, "guild_a", "user_1", year_month="2026-05")
    grant_monthly_token(in_memory_db, "guild_b", "user_1", year_month="2026-05")
    tok_a = available_token(
        in_memory_db, "guild_a", "user_1", year_month="2026-05"
    )
    consume_token(
        in_memory_db, tok_a["id"], target_user_id="t1", post_id="p1"
    )
    # guild_b should see no consumption even though same user_id.
    assert last_consumed_token(in_memory_db, "guild_b", "user_1") is None
    last_a = last_consumed_token(in_memory_db, "guild_a", "user_1")
    assert last_a is not None
    assert last_a["guild_id"] == "guild_a"


# ---------------------------------------------------------------------------
# consume_token + refund_token
# ---------------------------------------------------------------------------


def test_consume_token_marks_consumed(in_memory_db):
    grant_monthly_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    tok = available_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    assert consume_token(
        in_memory_db, tok["id"], target_user_id="t1", post_id="p1"
    ) is True
    row = in_memory_db.execute(
        "SELECT consumed_at, consumed_target_user_id, consumed_on_post_id"
        " FROM discord_peer_roast_tokens WHERE id = ?",
        (tok["id"],),
    ).fetchone()
    assert row["consumed_at"] is not None
    assert row["consumed_target_user_id"] == "t1"
    assert row["consumed_on_post_id"] == "p1"


def test_consume_token_no_op_when_already_consumed(in_memory_db):
    grant_monthly_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    tok = available_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    assert consume_token(
        in_memory_db, tok["id"], target_user_id="t1", post_id="p1"
    ) is True
    assert consume_token(
        in_memory_db, tok["id"], target_user_id="t2", post_id="p2"
    ) is False
    # Original consumption preserved.
    row = in_memory_db.execute(
        "SELECT consumed_target_user_id FROM discord_peer_roast_tokens WHERE id = ?",
        (tok["id"],),
    ).fetchone()
    assert row["consumed_target_user_id"] == "t1"


def test_refund_token_clears_consumption_fields(in_memory_db):
    grant_monthly_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    tok = available_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    consume_token(in_memory_db, tok["id"], target_user_id="t1", post_id="p1")
    assert refund_token(in_memory_db, tok["id"]) is True
    row = in_memory_db.execute(
        "SELECT consumed_at, consumed_target_user_id, consumed_on_post_id"
        " FROM discord_peer_roast_tokens WHERE id = ?",
        (tok["id"],),
    ).fetchone()
    # Refund leaves no audit-residue per plan §5.2.
    assert row["consumed_at"] is None
    assert row["consumed_target_user_id"] is None
    assert row["consumed_on_post_id"] is None


def test_refund_token_no_op_when_not_consumed(in_memory_db):
    grant_monthly_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    tok = available_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    assert refund_token(in_memory_db, tok["id"]) is False


def test_refunded_token_becomes_available_again(in_memory_db):
    grant_monthly_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    tok = available_token(in_memory_db, "guild_1", "user_1", year_month="2026-05")
    consume_token(in_memory_db, tok["id"], target_user_id="t1", post_id="p1")
    refund_token(in_memory_db, tok["id"])
    again = available_token(
        in_memory_db, "guild_1", "user_1", year_month="2026-05"
    )
    assert again is not None
    assert again["id"] == tok["id"]


# ---------------------------------------------------------------------------
# count_target_peer_roasts_this_month + cooldown_active_between
# ---------------------------------------------------------------------------


def _consume_against_target(
    conn, *, guild_id, actor_user_id, target_user_id, year_month,
    consumed_at: str,
) -> int:
    grant_monthly_token(conn, guild_id, actor_user_id, year_month=year_month)
    # Reset month so we can grant a second one for the same actor in tests
    # that need multiple consumptions. The unique key includes year_month, so
    # we manually update the freshly-granted row's consumed_at to the desired
    # historical timestamp to simulate consumption timing.
    row = conn.execute(
        "SELECT id FROM discord_peer_roast_tokens"
        " WHERE guild_id = ? AND actor_user_id = ? AND year_month = ?"
        " AND consumed_at IS NULL ORDER BY id ASC LIMIT 1",
        (guild_id, actor_user_id, year_month),
    ).fetchone()
    consume_token(
        conn, row["id"], target_user_id=target_user_id, post_id=f"p_{row['id']}"
    )
    conn.execute(
        text(
            "UPDATE discord_peer_roast_tokens SET consumed_at = :ts WHERE id = :id"
        ),
        {"ts": consumed_at, "id": row["id"]},
    )
    conn.commit()
    return int(row["id"])


def test_count_target_peer_roasts_uses_consumed_at(in_memory_db):
    # Two consumptions against the same target in May, by different actors.
    _consume_against_target(
        in_memory_db,
        guild_id="guild_1", actor_user_id="actor_a",
        target_user_id="target_x", year_month="2026-05",
        consumed_at="2026-05-10T12:00:00Z",
    )
    _consume_against_target(
        in_memory_db,
        guild_id="guild_1", actor_user_id="actor_b",
        target_user_id="target_x", year_month="2026-05",
        consumed_at="2026-05-20T12:00:00Z",
    )
    assert count_target_peer_roasts_this_month(
        in_memory_db, "guild_1", "target_x", year_month="2026-05"
    ) == 2


def test_count_target_peer_roasts_filters_by_calendar_month(in_memory_db):
    # April-granted token consumed in May — counts for May, not April.
    _consume_against_target(
        in_memory_db,
        guild_id="guild_1", actor_user_id="actor_a",
        target_user_id="target_x", year_month="2026-04",
        consumed_at="2026-05-02T10:00:00Z",
    )
    assert count_target_peer_roasts_this_month(
        in_memory_db, "guild_1", "target_x", year_month="2026-05"
    ) == 1
    assert count_target_peer_roasts_this_month(
        in_memory_db, "guild_1", "target_x", year_month="2026-04"
    ) == 0


def test_count_target_peer_roasts_excludes_unspent(in_memory_db):
    grant_monthly_token(in_memory_db, "guild_1", "actor_a", year_month="2026-05")
    assert count_target_peer_roasts_this_month(
        in_memory_db, "guild_1", "target_x", year_month="2026-05"
    ) == 0


def test_count_target_peer_roasts_isolated_per_guild(in_memory_db):
    _consume_against_target(
        in_memory_db,
        guild_id="guild_a", actor_user_id="actor_a",
        target_user_id="target_x", year_month="2026-05",
        consumed_at="2026-05-10T12:00:00Z",
    )
    assert count_target_peer_roasts_this_month(
        in_memory_db, "guild_a", "target_x", year_month="2026-05"
    ) == 1
    assert count_target_peer_roasts_this_month(
        in_memory_db, "guild_b", "target_x", year_month="2026-05"
    ) == 0


def test_count_target_handles_year_rollover():
    # Use a fresh CompatConnection so we can control consumed_at directly.
    from tests.conftest import make_test_conn

    conn = make_test_conn()
    try:
        _consume_against_target(
            conn,
            guild_id="guild_1", actor_user_id="actor_a",
            target_user_id="target_x", year_month="2026-12",
            consumed_at="2026-12-31T23:59:00Z",
        )
        _consume_against_target(
            conn,
            guild_id="guild_1", actor_user_id="actor_b",
            target_user_id="target_x", year_month="2026-12",
            consumed_at="2027-01-01T00:00:01Z",
        )
        assert count_target_peer_roasts_this_month(
            conn, "guild_1", "target_x", year_month="2026-12"
        ) == 1
        assert count_target_peer_roasts_this_month(
            conn, "guild_1", "target_x", year_month="2027-01"
        ) == 1
    finally:
        conn.close()


def test_cooldown_active_between_true_within_window(in_memory_db):
    _consume_against_target(
        in_memory_db,
        guild_id="guild_1", actor_user_id="actor_a",
        target_user_id="target_x", year_month="2026-05",
        consumed_at=(
            datetime.now(timezone.utc) - timedelta(days=10)
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    assert cooldown_active_between(
        in_memory_db, "guild_1", "actor_a", "target_x", within_days=90
    ) is True


def test_cooldown_active_between_false_outside_window(in_memory_db):
    _consume_against_target(
        in_memory_db,
        guild_id="guild_1", actor_user_id="actor_a",
        target_user_id="target_x", year_month="2026-01",
        consumed_at=(
            datetime.now(timezone.utc) - timedelta(days=200)
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    assert cooldown_active_between(
        in_memory_db, "guild_1", "actor_a", "target_x", within_days=90
    ) is False


def test_cooldown_isolated_per_actor_target(in_memory_db):
    _consume_against_target(
        in_memory_db,
        guild_id="guild_1", actor_user_id="actor_a",
        target_user_id="target_x", year_month="2026-05",
        consumed_at=(
            datetime.now(timezone.utc) - timedelta(days=5)
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    assert cooldown_active_between(
        in_memory_db, "guild_1", "actor_a", "target_x"
    ) is True
    # Different actor, same target — no cooldown.
    assert cooldown_active_between(
        in_memory_db, "guild_1", "actor_b", "target_x"
    ) is False
    # Same actor, different target — no cooldown.
    assert cooldown_active_between(
        in_memory_db, "guild_1", "actor_a", "target_y"
    ) is False


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def test_insert_flag_returns_new_id(in_memory_db):
    flag_id = insert_flag(
        in_memory_db,
        guild_id="guild_1",
        target_user_id="target_x",
        actor_user_id="actor_a",
        post_id="p1",
        bot_reply_id="br1",
        reactor_user_id="reactor_z",
    )
    assert isinstance(flag_id, int) and flag_id > 0


def test_insert_flag_persists_all_attribution_fields(in_memory_db):
    insert_flag(
        in_memory_db,
        guild_id="guild_1", target_user_id="target_x", actor_user_id="actor_a",
        post_id="p1", bot_reply_id="br1", reactor_user_id="target_x",
    )
    row = in_memory_db.execute(
        "SELECT * FROM discord_peer_roast_flags WHERE post_id = 'p1'"
    ).fetchone()
    assert row["reactor_user_id"] == "target_x"  # self-flag
    assert row["bot_reply_id"] == "br1"


def test_list_flags_filters_by_target(in_memory_db):
    insert_flag(
        in_memory_db,
        guild_id="guild_1", target_user_id="target_x", actor_user_id="actor_a",
        post_id="p1", bot_reply_id="br1", reactor_user_id="r1",
    )
    insert_flag(
        in_memory_db,
        guild_id="guild_1", target_user_id="target_y", actor_user_id="actor_a",
        post_id="p2", bot_reply_id="br2", reactor_user_id="r2",
    )
    flagged_x = list_flags(in_memory_db, "guild_1", target_user_id="target_x")
    assert {r["bot_reply_id"] for r in flagged_x} == {"br1"}


def test_list_flags_isolated_per_guild(in_memory_db):
    insert_flag(
        in_memory_db,
        guild_id="guild_a", target_user_id="target_x", actor_user_id="actor_a",
        post_id="p1", bot_reply_id="br1", reactor_user_id="r1",
    )
    assert list_flags(in_memory_db, "guild_b") == []


# ---------------------------------------------------------------------------
# aggregate_peer_roast_report
# ---------------------------------------------------------------------------


def _insert_roast_audit(
    conn,
    *,
    guild_id: str,
    user_id: str,
    actor_user_id: str,
    post_id: str,
    invocation_path: str,
    timestamp: str | None = None,
) -> int:
    """Insert a fitcheck_roast_generated audit row and return its id."""
    import json
    detail = json.dumps({
        "guild_id": guild_id,
        "user_id": user_id,
        "actor_user_id": actor_user_id,
        "post_id": post_id,
        "invocation_path": invocation_path,
    })
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    row = conn.execute(
        text(
            "INSERT INTO audit_log (actor, action, org_id, detail_json, source, timestamp)"
            " VALUES (:actor, 'fitcheck_roast_generated', :org, :d, 'sable-roles', :ts)"
            " RETURNING id"
        ),
        {"actor": "discord:bot:auto", "org": "test_org", "d": detail, "ts": ts},
    ).fetchone()
    conn.commit()
    return int(row["id"])


def _insert_reply_audit(
    conn, *, audit_log_id: int, bot_reply_id: str,
    timestamp: str | None = None,
) -> None:
    import json
    detail = json.dumps({"audit_log_id": audit_log_id, "bot_reply_id": bot_reply_id})
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        text(
            "INSERT INTO audit_log (actor, action, org_id, detail_json, source, timestamp)"
            " VALUES (:actor, 'fitcheck_roast_replied', :org, :d, 'sable-roles', :ts)"
        ),
        {"actor": "discord:bot:auto", "org": "test_org", "d": detail, "ts": ts},
    )
    conn.commit()


# ---------------------------------------------------------------------------
# find_peer_roast_for_bot_reply (R7)
# ---------------------------------------------------------------------------


def test_find_peer_roast_returns_none_when_no_reply_row(in_memory_db):
    """Without a matching `fitcheck_roast_replied` row, lookup is None."""
    assert find_peer_roast_for_bot_reply(in_memory_db, "no_such") is None


def test_find_peer_roast_returns_match_for_peer_path(in_memory_db):
    aid = _insert_roast_audit(
        in_memory_db,
        guild_id="guild_1", user_id="target_x", actor_user_id="actor_a",
        post_id="p1", invocation_path="peer_roast",
    )
    _insert_reply_audit(in_memory_db, audit_log_id=aid, bot_reply_id="br1")
    match = find_peer_roast_for_bot_reply(in_memory_db, "br1")
    assert match is not None
    assert match["target_user_id"] == "target_x"
    assert match["actor_user_id"] == "actor_a"
    assert match["post_id"] == "p1"
    assert match["guild_id"] == "guild_1"
    assert match["invocation_path"] == "peer_roast"


def test_find_peer_roast_ignores_optin_paths(in_memory_db):
    """Opt-in / random / mod-roast replies are NOT flag-eligible per plan §8.2."""
    aid = _insert_roast_audit(
        in_memory_db,
        guild_id="guild_1", user_id="target_x", actor_user_id=None,
        post_id="p1", invocation_path="optin_once",
    )
    _insert_reply_audit(in_memory_db, audit_log_id=aid, bot_reply_id="br1")
    assert find_peer_roast_for_bot_reply(in_memory_db, "br1") is None


def test_find_peer_roast_ignores_mod_roast_paths(in_memory_db):
    """Mod-roast replies must NOT be flag-eligible — confirms the
    invocation_path filter blocks them even if a reply audit row was
    erroneously written for a mod-roast (defense-in-depth)."""
    aid = _insert_roast_audit(
        in_memory_db,
        guild_id="guild_1", user_id="target_x", actor_user_id="mod_a",
        post_id="p1", invocation_path="mod_roast",
    )
    _insert_reply_audit(in_memory_db, audit_log_id=aid, bot_reply_id="br1")
    assert find_peer_roast_for_bot_reply(in_memory_db, "br1") is None


def test_find_peer_roast_includes_restored_path(in_memory_db):
    """peer_roast_restored (R8 streak-restoration consumption) is flag-eligible."""
    aid = _insert_roast_audit(
        in_memory_db,
        guild_id="guild_1", user_id="target_x", actor_user_id="actor_a",
        post_id="p1", invocation_path="peer_roast_restored",
    )
    _insert_reply_audit(in_memory_db, audit_log_id=aid, bot_reply_id="br1")
    match = find_peer_roast_for_bot_reply(in_memory_db, "br1")
    assert match is not None
    assert match["invocation_path"] == "peer_roast_restored"


def test_find_peer_roast_returns_none_for_unknown_bot_reply(in_memory_db):
    aid = _insert_roast_audit(
        in_memory_db,
        guild_id="guild_1", user_id="target_x", actor_user_id="actor_a",
        post_id="p1", invocation_path="peer_roast",
    )
    _insert_reply_audit(in_memory_db, audit_log_id=aid, bot_reply_id="br1")
    # Different bot_reply_id → no match
    assert find_peer_roast_for_bot_reply(in_memory_db, "br_other") is None


# ---------------------------------------------------------------------------
# aggregate_peer_roast_report (R1, additional coverage)
# ---------------------------------------------------------------------------


def test_aggregate_peer_roast_report_groups_by_pair(in_memory_db):
    aid1 = _insert_roast_audit(
        in_memory_db,
        guild_id="guild_1", user_id="target_x", actor_user_id="actor_a",
        post_id="p1", invocation_path="peer_roast",
    )
    aid2 = _insert_roast_audit(
        in_memory_db,
        guild_id="guild_1", user_id="target_x", actor_user_id="actor_a",
        post_id="p2", invocation_path="peer_roast",
    )
    _insert_reply_audit(in_memory_db, audit_log_id=aid1, bot_reply_id="br1")
    _insert_reply_audit(in_memory_db, audit_log_id=aid2, bot_reply_id="br2")
    insert_flag(
        in_memory_db,
        guild_id="guild_1", target_user_id="target_x", actor_user_id="actor_a",
        post_id="p1", bot_reply_id="br1", reactor_user_id="target_x",
    )
    rows = aggregate_peer_roast_report(in_memory_db, "guild_1", lookback_days=30)
    assert len(rows) == 1
    assert rows[0]["actor_user_id"] == "actor_a"
    assert rows[0]["target_user_id"] == "target_x"
    assert rows[0]["n"] == 2
    assert rows[0]["flag_count"] == 1
    assert rows[0]["self_flag_count"] == 1


def test_aggregate_peer_roast_report_excludes_optin_paths(in_memory_db):
    aid = _insert_roast_audit(
        in_memory_db,
        guild_id="guild_1", user_id="target_x", actor_user_id=None,
        post_id="p1", invocation_path="optin_once",
    )
    _insert_reply_audit(in_memory_db, audit_log_id=aid, bot_reply_id="br1")
    rows = aggregate_peer_roast_report(in_memory_db, "guild_1", lookback_days=30)
    assert rows == []


def test_aggregate_peer_roast_report_isolated_per_guild(in_memory_db):
    aid = _insert_roast_audit(
        in_memory_db,
        guild_id="guild_a", user_id="target_x", actor_user_id="actor_a",
        post_id="p1", invocation_path="peer_roast",
    )
    _insert_reply_audit(in_memory_db, audit_log_id=aid, bot_reply_id="br1")
    rows_a = aggregate_peer_roast_report(in_memory_db, "guild_a", lookback_days=30)
    rows_b = aggregate_peer_roast_report(in_memory_db, "guild_b", lookback_days=30)
    assert len(rows_a) == 1
    assert rows_b == []


def test_aggregate_peer_roast_report_respects_lookback(in_memory_db):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%S")
    _insert_roast_audit(
        in_memory_db,
        guild_id="guild_1", user_id="target_x", actor_user_id="actor_a",
        post_id="p1", invocation_path="peer_roast", timestamp=old_ts,
    )
    rows = aggregate_peer_roast_report(in_memory_db, "guild_1", lookback_days=30)
    assert rows == []
    rows = aggregate_peer_roast_report(in_memory_db, "guild_1", lookback_days=60)
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# CHECK constraint
# ---------------------------------------------------------------------------


def test_token_source_check_constraint_blocks_invalid(in_memory_db):
    """Bypass the helper to confirm SQL-level CHECK still fires."""
    with pytest.raises(Exception):
        in_memory_db.execute(
            text(
                "INSERT INTO discord_peer_roast_tokens"
                " (guild_id, actor_user_id, source, year_month)"
                " VALUES ('g', 'u', 'bogus_source', '2026-05')"
            )
        )
        in_memory_db.commit()
