"""Tests for discord_fitcheck_scores + image_phash helpers (Scored Mode V2)."""
from __future__ import annotations

from sqlalchemy import text

from sable_platform.db.discord_fitcheck_scores import (
    count_pool_size,
    fetch_curve_pool_raw_totals,
    get_score,
    invalidate_score,
    list_recent_phashes_for_collision,
    record_score_failure,
    set_phash_on_streak_event,
    upsert_score_success,
)
from sable_platform.db.discord_streaks import upsert_streak_event


def _seed_streak_event(
    conn,
    org_id: str,
    *,
    post_id: str,
    user_id: str = "user_1",
    day: str = "2026-05-10",
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


def _success_kwargs(
    *,
    org_id: str,
    post_id: str,
    user_id: str = "user_1",
    posted_at: str = "2026-05-10T12:00:00Z",
    scored_at: str = "2026-05-10T12:00:05Z",
    axis_cohesion: int = 7,
    axis_execution: int = 8,
    axis_concept: int = 6,
    axis_catch: int = 5,
    catch_detected: str | None = None,
    catch_naming_class: str | None = None,
    description: str | None = "neutral fit description.",
    confidence: float | None = 0.85,
    curve_basis: str = "absolute",
    pool_size: int = 0,
    percentile: float = 65.0,
) -> dict:
    return {
        "org_id": org_id,
        "guild_id": "guild_1",
        "post_id": post_id,
        "user_id": user_id,
        "posted_at": posted_at,
        "scored_at": scored_at,
        "model_id": "claude-sonnet-4-6",
        "prompt_version": "rubric_v1",
        "axis_cohesion": axis_cohesion,
        "axis_execution": axis_execution,
        "axis_concept": axis_concept,
        "axis_catch": axis_catch,
        "raw_total": axis_cohesion + axis_execution + axis_concept + axis_catch,
        "catch_detected": catch_detected,
        "catch_naming_class": catch_naming_class,
        "description": description,
        "confidence": confidence,
        "axis_rationales_json": '{"cohesion": "...", "execution": "...", "concept": "...", "catch": "..."}',
        "curve_basis": curve_basis,
        "pool_size_at_score_time": pool_size,
        "percentile": percentile,
    }


# ---------------------------------------------------------------------------
# set_phash_on_streak_event
# ---------------------------------------------------------------------------


def test_set_phash_stamps_a_streak_event(org_db):
    conn, org_id = org_db
    _seed_streak_event(conn, org_id, post_id="post_1")

    assert set_phash_on_streak_event(conn, "guild_1", "post_1", "abcdef0123456789") is True

    row = conn.execute(
        text("SELECT image_phash FROM discord_streak_events WHERE post_id = 'post_1'")
    ).fetchone()
    assert row[0] == "abcdef0123456789"


def test_set_phash_is_idempotent_and_immutable_once_set(org_db):
    conn, org_id = org_db
    _seed_streak_event(conn, org_id, post_id="post_1")
    assert set_phash_on_streak_event(conn, "guild_1", "post_1", "first_hash") is True
    # Second call MUST be a no-op (the bytes never change for the same post).
    assert set_phash_on_streak_event(conn, "guild_1", "post_1", "second_hash") is False

    row = conn.execute(
        text("SELECT image_phash FROM discord_streak_events WHERE post_id = 'post_1'")
    ).fetchone()
    assert row[0] == "first_hash"


def test_set_phash_returns_false_when_no_row(org_db):
    conn, org_id = org_db
    # No streak row seeded.
    assert set_phash_on_streak_event(conn, "guild_1", "missing_post", "hash") is False


# ---------------------------------------------------------------------------
# list_recent_phashes_for_collision
# ---------------------------------------------------------------------------


def test_list_recent_phashes_filters_nulls_and_other_orgs(org_db):
    conn, org_id = org_db
    other_org = "other_org_001"
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES (?, ?)", (other_org, "Other"))
    conn.commit()

    _seed_streak_event(conn, org_id, post_id="post_a")
    set_phash_on_streak_event(conn, "guild_1", "post_a", "aaaaaaaaaaaaaaaa")
    _seed_streak_event(conn, org_id, post_id="post_no_phash")  # phash NULL
    _seed_streak_event(conn, other_org, post_id="post_other")
    set_phash_on_streak_event(conn, "guild_1", "post_other", "bbbbbbbbbbbbbbbb")

    rows = list_recent_phashes_for_collision(conn, org_id, "2026-01-01T00:00:00Z")
    phashes = {r["post_id"] for r in rows}
    assert "post_a" in phashes
    assert "post_no_phash" not in phashes
    assert "post_other" not in phashes  # wrong org


def test_list_recent_phashes_excludes_self(org_db):
    conn, org_id = org_db
    _seed_streak_event(conn, org_id, post_id="post_a")
    set_phash_on_streak_event(conn, "guild_1", "post_a", "aaaaaaaaaaaaaaaa")
    _seed_streak_event(conn, org_id, post_id="post_b")
    set_phash_on_streak_event(conn, "guild_1", "post_b", "bbbbbbbbbbbbbbbb")

    rows = list_recent_phashes_for_collision(
        conn, org_id, "2026-01-01T00:00:00Z", exclude_post_id="post_a"
    )
    post_ids = {r["post_id"] for r in rows}
    assert "post_a" not in post_ids
    assert "post_b" in post_ids


# ---------------------------------------------------------------------------
# upsert_score_success
# ---------------------------------------------------------------------------


def test_upsert_score_success_creates_row(org_db):
    conn, org_id = org_db
    kwargs = _success_kwargs(org_id=org_id, post_id="post_1")
    upsert_score_success(conn, **kwargs)

    score = get_score(conn, "guild_1", "post_1")
    assert score is not None
    assert score["score_status"] == "success"
    assert score["axis_cohesion"] == 7
    assert score["axis_execution"] == 8
    assert score["axis_concept"] == 6
    assert score["axis_catch"] == 5
    assert score["raw_total"] == 26
    assert score["curve_basis"] == "absolute"
    assert score["percentile"] == 65.0
    assert score["score_error"] is None


def test_upsert_score_success_preserves_reveal_and_invalidated_on_conflict(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="post_1"))
    # Pretend Pass C wrote reveal columns + a mod invalidated.
    conn.execute(
        text(
            "UPDATE discord_fitcheck_scores"
            " SET reveal_eligible = 1,"
            "     reveal_fired_at = '2026-05-11T00:00:00Z',"
            "     reveal_post_id = 'reveal_post_xyz',"
            "     reveal_trigger = 'reactions',"
            "     invalidated_at = '2026-05-11T05:00:00Z',"
            "     invalidated_reason = 'mod review'"
            " WHERE post_id = 'post_1'"
        )
    )
    conn.commit()

    upsert_score_success(
        conn,
        **_success_kwargs(
            org_id=org_id,
            post_id="post_1",
            axis_cohesion=10,
            axis_execution=10,
            axis_concept=10,
            axis_catch=10,
            percentile=99.0,
        ),
    )

    score = get_score(conn, "guild_1", "post_1")
    assert score is not None
    # Re-score takes the new judgement.
    assert score["axis_cohesion"] == 10
    assert score["percentile"] == 99.0
    # ON CONFLICT preserved reveal + invalidated columns.
    assert score["reveal_eligible"] == 1
    assert score["reveal_fired_at"] == "2026-05-11T00:00:00Z"
    assert score["reveal_post_id"] == "reveal_post_xyz"
    assert score["reveal_trigger"] == "reactions"
    assert score["invalidated_at"] == "2026-05-11T05:00:00Z"
    assert score["invalidated_reason"] == "mod review"


# ---------------------------------------------------------------------------
# record_score_failure
# ---------------------------------------------------------------------------


def test_record_score_failure_creates_failed_row(org_db):
    conn, org_id = org_db
    record_score_failure(
        conn,
        org_id,
        "guild_1",
        "post_1",
        "user_1",
        "2026-05-10T12:00:00Z",
        "2026-05-10T12:00:10Z",
        "claude-sonnet-4-6",
        "rubric_v1",
        "api_error:APIError:rate limited",
    )

    score = get_score(conn, "guild_1", "post_1")
    assert score is not None
    assert score["score_status"] == "failed"
    assert score["score_error"] == "api_error:APIError:rate limited"
    assert score["axis_cohesion"] is None
    assert score["raw_total"] is None
    assert score["percentile"] is None


def test_failure_then_success_upgrades_row(org_db):
    conn, org_id = org_db
    record_score_failure(
        conn,
        org_id,
        "guild_1",
        "post_1",
        "user_1",
        "2026-05-10T12:00:00Z",
        "2026-05-10T12:00:10Z",
        "claude-sonnet-4-6",
        "rubric_v1",
        "transient",
    )
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="post_1"))

    score = get_score(conn, "guild_1", "post_1")
    assert score is not None
    assert score["score_status"] == "success"
    assert score["score_error"] is None
    assert score["axis_cohesion"] == 7


# ---------------------------------------------------------------------------
# count_pool_size / fetch_curve_pool_raw_totals
# ---------------------------------------------------------------------------


def test_count_pool_size_filters_failed_invalidated_and_window(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="post_1"))
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="post_2"))
    record_score_failure(
        conn, org_id, "guild_1", "post_3", "user_1",
        "2026-05-10T12:00:00Z", "2026-05-10T12:00:10Z",
        "claude-sonnet-4-6", "rubric_v1", "fail",
    )
    invalidate_score(conn, "guild_1", "post_1", reason="mod_invalidated")

    # Only post_2 (success + not invalidated) counts.
    assert count_pool_size(conn, org_id, "2026-01-01T00:00:00Z") == 1
    assert count_pool_size(conn, org_id, "2099-01-01T00:00:00Z") == 0  # future window


def test_fetch_curve_pool_returns_raw_totals_for_pool(org_db):
    conn, org_id = org_db
    for i, raw in enumerate([20, 25, 30, 35]):
        a = raw // 4
        rest = raw - 3 * a
        kwargs = _success_kwargs(org_id=org_id, post_id=f"post_{i}")
        kwargs["axis_cohesion"] = a
        kwargs["axis_execution"] = a
        kwargs["axis_concept"] = a
        kwargs["axis_catch"] = max(3, rest)  # respect floor=3
        kwargs["raw_total"] = a + a + a + max(3, rest)
        upsert_score_success(conn, **kwargs)
    expected = [3 * (r // 4) + max(3, r - 3 * (r // 4)) for r in [20, 25, 30, 35]]
    totals = fetch_curve_pool_raw_totals(conn, org_id, "2026-01-01T00:00:00Z")
    assert sorted(totals) == sorted(expected)


# ---------------------------------------------------------------------------
# invalidate_score
# ---------------------------------------------------------------------------


def test_invalidate_score_sets_columns_once(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="post_1"))

    assert invalidate_score(conn, "guild_1", "post_1", reason="community_signal") is True
    # Idempotent — second call returns False since already invalidated.
    assert invalidate_score(conn, "guild_1", "post_1", reason="other") is False

    score = get_score(conn, "guild_1", "post_1")
    assert score is not None
    assert score["invalidated_at"] is not None
    assert score["invalidated_reason"] == "community_signal"
