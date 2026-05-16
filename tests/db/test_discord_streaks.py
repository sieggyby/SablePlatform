"""Tests for discord_streak_events DB helpers."""
from __future__ import annotations

from sqlalchemy import text

from sable_platform.db.discord_streaks import (
    compute_streak_state,
    get_event,
    update_reaction_score,
    upsert_streak_event,
)


def _upsert(
    conn,
    org_id: str,
    *,
    post_id: str,
    user_id: str = "user_1",
    day: str = "2026-05-10",
    reaction_score: int | None = None,
) -> None:
    upsert_streak_event(
        conn,
        org_id,
        "guild_1",
        "channel_1",
        post_id,
        user_id,
        f"{day}T12:00:00Z",
        day,
        1,
        1,
    )
    if reaction_score is not None:
        conn.execute(
            text(
                "UPDATE discord_streak_events"
                " SET reaction_score = :score"
                " WHERE guild_id = 'guild_1' AND post_id = :post_id"
            ),
            {"score": reaction_score, "post_id": post_id},
        )
        conn.commit()


def test_upsert_creates_row(org_db):
    conn, org_id = org_db
    _upsert(conn, org_id, post_id="post_1")

    row = get_event(conn, "guild_1", "post_1")
    assert row is not None
    assert row["org_id"] == org_id
    assert row["channel_id"] == "channel_1"
    assert row["user_id"] == "user_1"
    assert row["counted_for_day"] == "2026-05-10"
    assert row["attachment_count"] == 1
    assert row["image_attachment_count"] == 1
    assert row["ingest_source"] == "gateway"
    assert row["reaction_score"] == 0


def test_upsert_is_idempotent_and_does_not_clobber_admin_or_reaction_fields(org_db):
    conn, org_id = org_db
    _upsert(conn, org_id, post_id="post_1")
    event = get_event(conn, "guild_1", "post_1")
    assert event is not None
    assert update_reaction_score(conn, "guild_1", "post_1", 8, event["updated_at"]) is True
    conn.execute(
        text(
            "UPDATE discord_streak_events"
            " SET counts_for_streak = 0,"
            "     invalidated_at = '2026-05-11T00:00:00Z',"
            "     invalidated_reason = 'mod removed'"
            " WHERE guild_id = 'guild_1' AND post_id = 'post_1'"
        )
    )
    conn.commit()

    _upsert(conn, org_id, post_id="post_1", day="2026-05-10")

    row = get_event(conn, "guild_1", "post_1")
    assert row is not None
    assert row["reaction_score"] == 8
    assert row["counts_for_streak"] == 0
    assert row["invalidated_at"] == "2026-05-11T00:00:00Z"
    assert row["invalidated_reason"] == "mod removed"


def test_update_reaction_score_uses_expected_updated_at_guard(org_db):
    conn, org_id = org_db
    _upsert(conn, org_id, post_id="post_1")
    event = get_event(conn, "guild_1", "post_1")
    assert event is not None

    assert update_reaction_score(conn, "guild_1", "post_1", 5, event["updated_at"]) is True
    updated = get_event(conn, "guild_1", "post_1")
    assert updated is not None
    assert updated["reaction_score"] == 5
    assert updated["updated_at"] != event["updated_at"]

    assert update_reaction_score(conn, "guild_1", "post_1", 7, event["updated_at"]) is False
    stale = get_event(conn, "guild_1", "post_1")
    assert stale is not None
    assert stale["reaction_score"] == 5


def test_compute_streak_state_handles_consecutive_days(org_db):
    conn, org_id = org_db
    for day, post_id in [
        ("2026-05-10", "post_1"),
        ("2026-05-11", "post_2"),
        ("2026-05-12", "post_3"),
    ]:
        _upsert(conn, org_id, post_id=post_id, day=day)

    state = compute_streak_state(conn, org_id, "user_1", as_of_day="2026-05-12")
    assert state["current_streak"] == 3
    assert state["longest_streak"] == 3
    assert state["total_fits"] == 3
    assert state["posted_today"] is True
    assert state["today_post_id"] == "post_3"


def test_compute_streak_state_resets_after_gap(org_db):
    conn, org_id = org_db
    _upsert(conn, org_id, post_id="post_1", day="2026-05-10")
    _upsert(conn, org_id, post_id="post_2", day="2026-05-12")

    state = compute_streak_state(conn, org_id, "user_1", as_of_day="2026-05-12")
    assert state["current_streak"] == 1
    assert state["longest_streak"] == 1


def test_compute_streak_state_counts_multiple_posts_same_day_once_for_streak(org_db):
    conn, org_id = org_db
    _upsert(conn, org_id, post_id="post_1", day="2026-05-12")
    _upsert(conn, org_id, post_id="post_2", day="2026-05-12")

    state = compute_streak_state(conn, org_id, "user_1", as_of_day="2026-05-12")
    assert state["current_streak"] == 1
    assert state["longest_streak"] == 1
    assert state["total_fits"] == 2
    assert state["posted_today"] is True


def test_compute_streak_state_handles_no_posts(org_db):
    conn, org_id = org_db
    state = compute_streak_state(conn, org_id, "user_1", as_of_day="2026-05-12")
    assert state == {
        "current_streak": 0,
        "longest_streak": 0,
        "total_fits": 0,
        "most_reacted_post_id": None,
        "most_reacted_reaction_count": 0,
        "most_reacted_channel_id": None,
        "most_reacted_guild_id": None,
        "today_post_id": None,
        "today_reaction_count": 0,
        "posted_today": False,
    }


def test_compute_streak_state_is_zero_when_no_fit_today_or_yesterday(org_db):
    conn, org_id = org_db
    _upsert(conn, org_id, post_id="post_1", day="2026-05-10")

    state = compute_streak_state(conn, org_id, "user_1", as_of_day="2026-05-12")
    assert state["current_streak"] == 0
    assert state["longest_streak"] == 1
    assert state["posted_today"] is False


def test_compute_streak_state_continues_from_yesterday_when_no_fit_today(org_db):
    """No fit today but fit yesterday — current_streak continues from yesterday (plan §3)."""
    conn, org_id = org_db
    _upsert(conn, org_id, post_id="post_1", day="2026-05-10")
    _upsert(conn, org_id, post_id="post_2", day="2026-05-11")

    state = compute_streak_state(conn, org_id, "user_1", as_of_day="2026-05-12")
    assert state["current_streak"] == 2
    assert state["longest_streak"] == 2
    assert state["posted_today"] is False
    assert state["today_post_id"] is None


def test_compute_streak_state_excludes_invalidated_rows(org_db):
    conn, org_id = org_db
    _upsert(conn, org_id, post_id="post_1", day="2026-05-11")
    _upsert(conn, org_id, post_id="post_2", day="2026-05-12")
    conn.execute(
        text(
            "UPDATE discord_streak_events"
            " SET counts_for_streak = 0, invalidated_at = '2026-05-12T13:00:00Z'"
            " WHERE post_id = 'post_2'"
        )
    )
    conn.commit()

    state = compute_streak_state(conn, org_id, "user_1", as_of_day="2026-05-12")
    assert state["current_streak"] == 1
    assert state["posted_today"] is False
    assert state["today_post_id"] is None
    assert state["total_fits"] == 1


def test_compute_streak_state_tracks_longest_streak_in_past(org_db):
    conn, org_id = org_db
    for day, post_id in [
        ("2026-05-01", "post_1"),
        ("2026-05-02", "post_2"),
        ("2026-05-03", "post_3"),
        ("2026-05-05", "post_4"),
    ]:
        _upsert(conn, org_id, post_id=post_id, day=day)

    state = compute_streak_state(conn, org_id, "user_1", as_of_day="2026-05-05")
    assert state["current_streak"] == 1
    assert state["longest_streak"] == 3


def test_compute_streak_state_returns_best_fit_and_today_reactions(org_db):
    conn, org_id = org_db
    _upsert(conn, org_id, post_id="post_1", day="2026-05-11", reaction_score=4)
    _upsert(conn, org_id, post_id="post_2", day="2026-05-12", reaction_score=9)

    state = compute_streak_state(conn, org_id, "user_1", as_of_day="2026-05-12")
    assert state["most_reacted_post_id"] == "post_2"
    assert state["most_reacted_reaction_count"] == 9
    assert state["most_reacted_channel_id"] == "channel_1"
    assert state["most_reacted_guild_id"] == "guild_1"
    assert state["today_post_id"] == "post_2"
    assert state["today_reaction_count"] == 9
