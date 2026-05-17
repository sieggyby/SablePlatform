"""Tests for Pass C reveal-pipeline helpers in discord_fitcheck_scores.

mark_reveal_fired / mark_reveal_cancelled_deleted / record_emoji_milestone_crossing
/ list_emoji_milestone_crossings_for_post.
"""
from __future__ import annotations

from sqlalchemy import text

from sable_platform.db.discord_fitcheck_scores import (
    convert_pending_to_cancelled_deleted,
    get_score,
    list_emoji_milestone_crossings_for_post,
    mark_reveal_cancelled_deleted,
    mark_reveal_fired,
    mark_reveal_publish_failed,
    record_emoji_milestone_crossing,
    update_reveal_post_id,
    upsert_score_success,
)


def _success_kwargs(
    *,
    org_id: str,
    post_id: str,
    user_id: str = "user_1",
) -> dict:
    return {
        "org_id": org_id,
        "guild_id": "guild_1",
        "post_id": post_id,
        "user_id": user_id,
        "posted_at": "2026-05-12T12:00:00Z",
        "scored_at": "2026-05-12T12:00:05Z",
        "model_id": "claude-sonnet-4-6",
        "prompt_version": "rubric_v1",
        "axis_cohesion": 7,
        "axis_execution": 8,
        "axis_concept": 6,
        "axis_catch": 5,
        "raw_total": 26,
        "catch_detected": None,
        "catch_naming_class": None,
        "description": "neutral fit",
        "confidence": 0.85,
        "axis_rationales_json": '{"cohesion": "a", "execution": "b", "concept": "c", "catch": "d"}',
        "curve_basis": "absolute",
        "pool_size_at_score_time": 0,
        "percentile": 65.0,
    }


# ---------------------------------------------------------------------------
# mark_reveal_fired — one-and-done CAS
# ---------------------------------------------------------------------------


def test_mark_reveal_fired_locks_unrevealed_row(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1"))

    won = mark_reveal_fired(conn, "guild_1", "p1", "reveal_msg_42", "reactions")
    assert won is True

    score = get_score(conn, "guild_1", "p1")
    assert score is not None
    assert score["reveal_fired_at"] is not None
    assert score["reveal_post_id"] == "reveal_msg_42"
    assert score["reveal_trigger"] == "reactions"
    assert score["reveal_eligible"] == 1


def test_mark_reveal_fired_second_caller_loses_race(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1"))

    first = mark_reveal_fired(conn, "guild_1", "p1", "reveal_msg_42", "reactions")
    second = mark_reveal_fired(conn, "guild_1", "p1", "reveal_msg_43", "reactions")
    assert first is True
    assert second is False

    score = get_score(conn, "guild_1", "p1")
    # The first reveal's post_id survives — second caller did NOT clobber.
    assert score["reveal_post_id"] == "reveal_msg_42"


def test_mark_reveal_fired_returns_false_when_no_row(org_db):
    conn, _org_id = org_db
    # No score row exists.
    won = mark_reveal_fired(conn, "guild_1", "missing", "msg", "reactions")
    assert won is False


def test_mark_reveal_fired_after_cancelled_deleted_is_no_op(org_db):
    """Once mark_reveal_cancelled_deleted has tripped the lock, a later
    reveal-fire attempt must NOT overwrite. One-and-done is symmetric.
    """
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1"))

    cancelled = mark_reveal_cancelled_deleted(conn, "guild_1", "p1")
    assert cancelled is True

    # An out-of-order reveal-fire attempt must lose.
    fired = mark_reveal_fired(conn, "guild_1", "p1", "msg_xyz", "reactions")
    assert fired is False

    score = get_score(conn, "guild_1", "p1")
    assert score["reveal_trigger"] == "cancelled_deleted"
    assert score["reveal_post_id"] is None


# ---------------------------------------------------------------------------
# mark_reveal_cancelled_deleted — one-and-done CAS
# ---------------------------------------------------------------------------


def test_mark_reveal_cancelled_deleted_locks_unrevealed_row(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1"))

    won = mark_reveal_cancelled_deleted(conn, "guild_1", "p1")
    assert won is True

    score = get_score(conn, "guild_1", "p1")
    assert score["reveal_fired_at"] is not None
    assert score["reveal_post_id"] is None
    assert score["reveal_trigger"] == "cancelled_deleted"


def test_mark_reveal_cancelled_deleted_second_caller_loses(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1"))

    first = mark_reveal_cancelled_deleted(conn, "guild_1", "p1")
    second = mark_reveal_cancelled_deleted(conn, "guild_1", "p1")
    assert first is True
    assert second is False


def test_mark_reveal_cancelled_after_fire_is_no_op(org_db):
    """If a reveal has already fired and the post is later deleted, the
    cancelled_deleted helper must NOT clobber the real reveal record.
    """
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1"))
    mark_reveal_fired(conn, "guild_1", "p1", "reveal_msg_42", "reactions")

    # Now the post gets deleted. Cancellation must lose.
    cancelled = mark_reveal_cancelled_deleted(conn, "guild_1", "p1")
    assert cancelled is False

    score = get_score(conn, "guild_1", "p1")
    assert score["reveal_trigger"] == "reactions"
    assert score["reveal_post_id"] == "reveal_msg_42"


# ---------------------------------------------------------------------------
# record_emoji_milestone_crossing — idempotent INSERT ... ON CONFLICT NOTHING
# ---------------------------------------------------------------------------


def test_record_emoji_milestone_crossing_first_insert_returns_true(org_db):
    conn, org_id = org_db
    inserted = record_emoji_milestone_crossing(
        conn, org_id, "guild_1", "p1", "🔥", 5
    )
    assert inserted is True

    row = conn.execute(
        text(
            "SELECT emoji_key, milestone FROM discord_fitcheck_emoji_milestones"
            " WHERE post_id = 'p1'"
        )
    ).fetchone()
    assert row is not None
    # CompatConnection rows expose mapping access.
    assert row["emoji_key"] == "🔥"
    assert row["milestone"] == 5


def test_record_emoji_milestone_crossing_duplicate_returns_false(org_db):
    conn, org_id = org_db
    first = record_emoji_milestone_crossing(
        conn, org_id, "guild_1", "p1", "🔥", 5
    )
    second = record_emoji_milestone_crossing(
        conn, org_id, "guild_1", "p1", "🔥", 5
    )
    assert first is True
    assert second is False

    # Only ONE row landed.
    rows = conn.execute(
        text(
            "SELECT COUNT(*) AS n FROM discord_fitcheck_emoji_milestones"
            " WHERE post_id = 'p1' AND emoji_key = '🔥' AND milestone = 5"
        )
    ).fetchone()
    assert rows["n"] == 1


def test_milestones_partition_per_emoji_and_per_level(org_db):
    """Different emoji on the same post, AND different milestones on the
    same emoji, are independent crossings.
    """
    conn, org_id = org_db
    assert record_emoji_milestone_crossing(conn, org_id, "guild_1", "p1", "🔥", 5)
    assert record_emoji_milestone_crossing(conn, org_id, "guild_1", "p1", "🔥", 8)
    assert record_emoji_milestone_crossing(conn, org_id, "guild_1", "p1", "💯", 5)
    # All four rows above are independent — none should collide.

    crossings = list_emoji_milestone_crossings_for_post(conn, "guild_1", "p1")
    keys = {(c["emoji_key"], c["milestone"]) for c in crossings}
    assert keys == {("🔥", 5), ("🔥", 8), ("💯", 5)}


def test_record_emoji_milestone_custom_emoji_string_form(org_db):
    """Custom server emoji come through discord.py as `<:name:id>`.
    The helper persists them verbatim — caller's canonical-form contract.
    """
    conn, org_id = org_db
    custom = "<:stitzy_yes:1501234567890>"
    assert record_emoji_milestone_crossing(conn, org_id, "guild_1", "p1", custom, 10)
    crossings = list_emoji_milestone_crossings_for_post(conn, "guild_1", "p1")
    assert any(c["emoji_key"] == custom for c in crossings)


def test_list_emoji_milestone_crossings_empty_for_unknown_post(org_db):
    conn, _org_id = org_db
    rows = list_emoji_milestone_crossings_for_post(conn, "guild_1", "no_such_post")
    assert rows == []


# ---------------------------------------------------------------------------
# upsert_score_success preserves Pass C reveal columns on conflict (covered
# in test_discord_fitcheck_scores already — re-asserted via cross-helper round-
# trip here for the Pass C cancel + re-score corner).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# update_reveal_post_id — guarded patch from 'pending' placeholder
# ---------------------------------------------------------------------------


def test_update_reveal_post_id_swaps_pending_for_real(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1"))
    mark_reveal_fired(conn, "guild_1", "p1", "pending", "reactions")

    swapped = update_reveal_post_id(conn, "guild_1", "p1", "real_msg_12345")
    assert swapped is True

    score = get_score(conn, "guild_1", "p1")
    assert score["reveal_post_id"] == "real_msg_12345"
    assert score["reveal_trigger"] == "reactions"


def test_update_reveal_post_id_refuses_to_clobber_real_id(org_db):
    """Once update_reveal_post_id has swapped 'pending' for a real id, a
    second call (e.g. retry, manual SQL) must NOT clobber.
    """
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1"))
    mark_reveal_fired(conn, "guild_1", "p1", "pending", "reactions")
    update_reveal_post_id(conn, "guild_1", "p1", "real_msg_first")
    swapped = update_reveal_post_id(conn, "guild_1", "p1", "real_msg_second")
    assert swapped is False

    score = get_score(conn, "guild_1", "p1")
    assert score["reveal_post_id"] == "real_msg_first"


def test_update_reveal_post_id_no_op_when_no_lock(org_db):
    conn, _ = org_db
    swapped = update_reveal_post_id(conn, "guild_1", "missing", "real_msg")
    assert swapped is False


# ---------------------------------------------------------------------------
# mark_reveal_publish_failed — guarded 'pending' → 'publish_failed'
# ---------------------------------------------------------------------------


def test_mark_reveal_publish_failed_converts_pending_lock(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1"))
    mark_reveal_fired(conn, "guild_1", "p1", "pending", "reactions")

    flipped = mark_reveal_publish_failed(conn, "guild_1", "p1")
    assert flipped is True

    score = get_score(conn, "guild_1", "p1")
    assert score["reveal_trigger"] == "publish_failed"
    assert score["reveal_post_id"] is None
    assert score["reveal_fired_at"] is not None  # lock preserved


def test_mark_reveal_publish_failed_refuses_to_clobber_finalised_reveal(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1"))
    mark_reveal_fired(conn, "guild_1", "p1", "pending", "reactions")
    update_reveal_post_id(conn, "guild_1", "p1", "real_msg_42")

    flipped = mark_reveal_publish_failed(conn, "guild_1", "p1")
    assert flipped is False

    score = get_score(conn, "guild_1", "p1")
    assert score["reveal_trigger"] == "reactions"
    assert score["reveal_post_id"] == "real_msg_42"


# ---------------------------------------------------------------------------
# convert_pending_to_cancelled_deleted — guarded 404-during-publish handler
# ---------------------------------------------------------------------------


def test_convert_pending_to_cancelled_deleted_routes_404(org_db):
    """When publish replies with 404, the post was deleted DURING the
    publish window. This helper routes the lock to the correct trigger
    (cancelled_deleted) so the design-§6.4 HIGH-severity audit applies.
    """
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1"))
    mark_reveal_fired(conn, "guild_1", "p1", "pending", "reactions")

    converted = convert_pending_to_cancelled_deleted(conn, "guild_1", "p1")
    assert converted is True

    score = get_score(conn, "guild_1", "p1")
    assert score["reveal_trigger"] == "cancelled_deleted"
    assert score["reveal_post_id"] is None


def test_convert_pending_to_cancelled_deleted_refuses_finalised_reveal(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1"))
    mark_reveal_fired(conn, "guild_1", "p1", "pending", "reactions")
    update_reveal_post_id(conn, "guild_1", "p1", "real_msg_99")

    converted = convert_pending_to_cancelled_deleted(conn, "guild_1", "p1")
    assert converted is False

    score = get_score(conn, "guild_1", "p1")
    assert score["reveal_trigger"] == "reactions"
    assert score["reveal_post_id"] == "real_msg_99"


def test_rescore_does_not_unlock_cancelled_deleted(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1"))
    cancelled = mark_reveal_cancelled_deleted(conn, "guild_1", "p1")
    assert cancelled is True

    # Re-scoring the same post (mod retry, prompt rev) must NOT undo the
    # cancellation lock.
    new = _success_kwargs(org_id=org_id, post_id="p1")
    new["axis_cohesion"] = 10
    new["raw_total"] = 32
    new["percentile"] = 99.0
    upsert_score_success(conn, **new)

    score = get_score(conn, "guild_1", "p1")
    assert score["axis_cohesion"] == 10  # re-score landed
    assert score["reveal_trigger"] == "cancelled_deleted"  # preserved
    assert score["reveal_fired_at"] is not None
    assert score["reveal_post_id"] is None
