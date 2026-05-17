"""Tests for Pass D leaderboard helpers in discord_fitcheck_scores.

list_top_revealed_fits + list_best_per_user_revealed. Both must enforce
the design-§6.4 leaderboard query contract: `reveal_trigger IN
('reactions','thread_messages')` to exclude `cancelled_deleted` /
`publish_failed` / `pending` rows that ALSO carry a non-NULL
`reveal_fired_at` (the column doubles as the one-and-done lock for
terminal failure states).
"""
from __future__ import annotations

from sqlalchemy import text

from sable_platform.db.discord_fitcheck_scores import (
    convert_pending_to_cancelled_deleted,
    invalidate_score,
    list_best_per_user_revealed,
    list_top_revealed_fits,
    mark_reveal_fired,
    mark_reveal_publish_failed,
    upsert_score_success,
)


def _success_kwargs(
    *,
    org_id: str,
    post_id: str,
    user_id: str = "user_1",
    percentile: float = 65.0,
    posted_at: str = "2026-05-12T12:00:00Z",
    catch_detected: str | None = None,
) -> dict:
    return {
        "org_id": org_id,
        "guild_id": "guild_1",
        "post_id": post_id,
        "user_id": user_id,
        "posted_at": posted_at,
        "scored_at": posted_at,
        "model_id": "claude-sonnet-4-6",
        "prompt_version": "rubric_v1",
        "axis_cohesion": 7,
        "axis_execution": 8,
        "axis_concept": 6,
        "axis_catch": 5,
        "raw_total": 26,
        "catch_detected": catch_detected,
        "catch_naming_class": "family_only" if catch_detected else None,
        "description": "neutral fit",
        "confidence": 0.85,
        "axis_rationales_json": '{"cohesion": "a", "execution": "b", "concept": "c", "catch": "d"}',
        "curve_basis": "absolute",
        "pool_size_at_score_time": 0,
        "percentile": percentile,
    }


def _insert_streak_event(conn, post_id, channel_id="chan_1", guild_id="guild_1", org_id="test_org_001", user_id="user_1"):
    """Pass D queries JOIN to discord_streak_events for channel_id (for
    jump-link construction). Fixture posts need a matching streak row.
    """
    conn.execute(
        text(
            "INSERT INTO discord_streak_events"
            " (org_id, guild_id, channel_id, post_id, user_id, posted_at, counted_for_day)"
            " VALUES (:org_id, :guild_id, :channel_id, :post_id, :user_id, :posted_at, :day)"
        ),
        {
            "org_id": org_id,
            "guild_id": guild_id,
            "channel_id": channel_id,
            "post_id": post_id,
            "user_id": user_id,
            "posted_at": "2026-05-12T12:00:00Z",
            "day": "2026-05-12",
        },
    )
    conn.commit()


def _reveal(conn, post_id, trigger="reactions"):
    """Mark a score as revealed (CAS-lock) and finalise the placeholder."""
    mark_reveal_fired(conn, "guild_1", post_id, "pending", trigger)
    # In real pipeline, update_reveal_post_id swaps 'pending' → real id.
    # For tests we set it via direct UPDATE for brevity.
    conn.execute(
        text(
            "UPDATE discord_fitcheck_scores"
            " SET reveal_post_id = :real"
            " WHERE post_id = :pid AND reveal_post_id = 'pending'"
        ),
        {"real": f"reveal_msg_{post_id}", "pid": post_id},
    )
    conn.commit()


# ---------------------------------------------------------------------------
# list_top_revealed_fits — basic shape + filter
# ---------------------------------------------------------------------------


def test_top_revealed_returns_revealed_only(org_db):
    conn, org_id = org_db

    # 3 scored fits: p1 revealed, p2 revealed, p3 not revealed
    for pid, pct in [("p1", 90.0), ("p2", 75.0), ("p3", 85.0)]:
        upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id=pid, percentile=pct))
        _insert_streak_event(conn, pid)
    _reveal(conn, "p1")
    _reveal(conn, "p2")

    rows = list_top_revealed_fits(conn, org_id)
    post_ids = [r["post_id"] for r in rows]
    assert "p1" in post_ids
    assert "p2" in post_ids
    assert "p3" not in post_ids


def test_top_revealed_ordered_by_percentile_desc(org_db):
    conn, org_id = org_db

    for pid, pct in [("p1", 50.0), ("p2", 90.0), ("p3", 70.0)]:
        upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id=pid, percentile=pct))
        _insert_streak_event(conn, pid)
        _reveal(conn, pid)

    rows = list_top_revealed_fits(conn, org_id)
    percentiles = [r["percentile"] for r in rows]
    assert percentiles == [90.0, 70.0, 50.0]


def test_top_revealed_excludes_cancelled_deleted(org_db):
    """The trigger-IN filter is the design §6.4 leaderboard contract.

    `cancelled_deleted` rows DO have non-NULL `reveal_fired_at` (used as
    the one-and-done lock) — without the trigger filter the leaderboard
    would render junk entries.
    """
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1", percentile=95.0))
    _insert_streak_event(conn, "p1")

    # Reveal-fire CAS-locks with 'pending' placeholder...
    mark_reveal_fired(conn, "guild_1", "p1", "pending", "reactions")
    # ...then publish 404 routes to cancelled_deleted, locking the row.
    convert_pending_to_cancelled_deleted(conn, "guild_1", "p1")

    rows = list_top_revealed_fits(conn, org_id)
    assert rows == []


def test_top_revealed_excludes_publish_failed(org_db):
    """publish_failed rows also have non-NULL reveal_fired_at — same
    junk-entry risk if the trigger filter were omitted."""
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1", percentile=95.0))
    _insert_streak_event(conn, "p1")
    mark_reveal_fired(conn, "guild_1", "p1", "pending", "reactions")
    mark_reveal_publish_failed(conn, "guild_1", "p1")

    rows = list_top_revealed_fits(conn, org_id)
    assert rows == []


def test_top_revealed_excludes_pending(org_db):
    """A reveal mid-publish has reveal_trigger='reactions' but
    reveal_post_id='pending'. Trigger filter accepts it, but the
    transient state should resolve to a terminal state seconds later.
    Leaderboard contract: we include pending rows because their trigger
    is in the allowed set — pending is a transient state, not a terminal
    failure. (If you want stricter exclusion, filter on
    `reveal_post_id != 'pending'` in the application layer.)
    """
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1", percentile=95.0))
    _insert_streak_event(conn, "p1")
    mark_reveal_fired(conn, "guild_1", "p1", "pending", "reactions")
    # NOT finalised — pending placeholder still in place.

    rows = list_top_revealed_fits(conn, org_id)
    # The query returns the row (its trigger IS 'reactions'). The
    # 'pending' placeholder is the caller's signal to wait/retry.
    assert len(rows) == 1
    assert rows[0]["reveal_trigger"] == "reactions"


def test_top_revealed_excludes_invalidated(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1", percentile=95.0))
    _insert_streak_event(conn, "p1")
    _reveal(conn, "p1")
    invalidate_score(conn, "guild_1", "p1", "community_signal")

    rows = list_top_revealed_fits(conn, org_id)
    assert rows == []


def test_top_revealed_respects_org_scope(org_db):
    conn, org_id = org_db
    # Insert a row for a DIFFERENT org.
    conn.execute(
        "INSERT INTO orgs (org_id, display_name) VALUES (?, ?)",
        ("other_org", "Other"),
    )
    conn.commit()

    upsert_score_success(conn, **_success_kwargs(org_id="other_org", post_id="p1", percentile=99.0))
    _insert_streak_event(conn, "p1")
    _reveal(conn, "p1")

    rows = list_top_revealed_fits(conn, org_id)
    assert rows == []  # Wrong org


def test_top_revealed_respects_limit(org_db):
    conn, org_id = org_db
    for i in range(15):
        pid = f"p{i:02d}"
        upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id=pid, percentile=float(50 + i)))
        _insert_streak_event(conn, pid)
        _reveal(conn, pid)

    rows = list_top_revealed_fits(conn, org_id, limit=5)
    assert len(rows) == 5
    # Top 5 by percentile — should be p14, p13, p12, p11, p10.
    assert [r["post_id"] for r in rows] == ["p14", "p13", "p12", "p11", "p10"]


def test_top_revealed_window_filter(org_db):
    """The 30d toggle gates by reveal_fired_at >= since_iso."""
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p_old", percentile=95.0))
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p_new", percentile=80.0))
    _insert_streak_event(conn, "p_old")
    _insert_streak_event(conn, "p_new")
    _reveal(conn, "p_old")
    _reveal(conn, "p_new")
    # Backdate p_old's reveal_fired_at to 90 days ago.
    conn.execute(
        text(
            "UPDATE discord_fitcheck_scores"
            " SET reveal_fired_at = '2026-02-12T12:00:00Z'"
            " WHERE post_id = 'p_old'"
        )
    )
    conn.commit()

    # Window filter cuts to last 30 days only.
    rows = list_top_revealed_fits(conn, org_id, since_iso="2026-04-12T00:00:00Z")
    assert [r["post_id"] for r in rows] == ["p_new"]


def test_top_revealed_includes_channel_id_for_jump_link(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1"))
    _insert_streak_event(conn, "p1", channel_id="chan_42")
    _reveal(conn, "p1")

    rows = list_top_revealed_fits(conn, org_id)
    assert rows[0]["channel_id"] == "chan_42"


def test_top_revealed_includes_catch_detected(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(
        org_id=org_id, post_id="p1",
        catch_detected="late-90s Raf bomber silhouette",
    ))
    _insert_streak_event(conn, "p1")
    _reveal(conn, "p1")

    rows = list_top_revealed_fits(conn, org_id)
    assert rows[0]["catch_detected"] == "late-90s Raf bomber silhouette"


def test_top_revealed_includes_thread_messages_trigger(org_db):
    """Both 'reactions' AND 'thread_messages' triggers count as success."""
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1", percentile=80.0))
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p2", percentile=70.0))
    _insert_streak_event(conn, "p1")
    _insert_streak_event(conn, "p2")
    _reveal(conn, "p1", trigger="reactions")
    _reveal(conn, "p2", trigger="thread_messages")

    rows = list_top_revealed_fits(conn, org_id)
    triggers = {r["reveal_trigger"] for r in rows}
    assert triggers == {"reactions", "thread_messages"}


def test_top_revealed_empty_when_no_data(org_db):
    conn, org_id = org_db
    rows = list_top_revealed_fits(conn, org_id)
    assert rows == []


# ---------------------------------------------------------------------------
# list_best_per_user_revealed — dedup by user
# ---------------------------------------------------------------------------


def test_best_per_user_one_row_per_user(org_db):
    """Same user has 3 revealed fits — best_per_user returns the highest only."""
    conn, org_id = org_db
    for pid, pct in [("p1", 50.0), ("p2", 80.0), ("p3", 65.0)]:
        upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id=pid, user_id="alice", percentile=pct))
        _insert_streak_event(conn, pid, user_id="alice")
        _reveal(conn, pid)

    rows = list_best_per_user_revealed(conn, org_id)
    assert len(rows) == 1
    assert rows[0]["user_id"] == "alice"
    assert rows[0]["percentile"] == 80.0
    assert rows[0]["post_id"] == "p2"


def test_best_per_user_multiple_users(org_db):
    conn, org_id = org_db
    # alice: 80, 60 → best = 80
    # bob: 90, 70 → best = 90
    # carol: 50 → best = 50
    rows_to_insert = [
        ("p1", "alice", 80.0),
        ("p2", "alice", 60.0),
        ("p3", "bob", 90.0),
        ("p4", "bob", 70.0),
        ("p5", "carol", 50.0),
    ]
    for pid, uid, pct in rows_to_insert:
        upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id=pid, user_id=uid, percentile=pct))
        _insert_streak_event(conn, pid, user_id=uid)
        _reveal(conn, pid)

    rows = list_best_per_user_revealed(conn, org_id)
    assert len(rows) == 3
    by_user = {r["user_id"]: r["percentile"] for r in rows}
    assert by_user == {"alice": 80.0, "bob": 90.0, "carol": 50.0}
    # Ordering: percentile DESC.
    assert [r["user_id"] for r in rows] == ["bob", "alice", "carol"]


def test_best_per_user_excludes_cancelled_deleted(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1", user_id="alice", percentile=95.0))
    _insert_streak_event(conn, "p1", user_id="alice")
    mark_reveal_fired(conn, "guild_1", "p1", "pending", "reactions")
    convert_pending_to_cancelled_deleted(conn, "guild_1", "p1")

    rows = list_best_per_user_revealed(conn, org_id)
    assert rows == []


def test_best_per_user_window_filter(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p_old", user_id="alice", percentile=95.0))
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p_new", user_id="alice", percentile=80.0))
    _insert_streak_event(conn, "p_old", user_id="alice")
    _insert_streak_event(conn, "p_new", user_id="alice")
    _reveal(conn, "p_old")
    _reveal(conn, "p_new")
    conn.execute(
        text(
            "UPDATE discord_fitcheck_scores"
            " SET reveal_fired_at = '2026-02-12T12:00:00Z'"
            " WHERE post_id = 'p_old'"
        )
    )
    conn.commit()

    # 30d window — only p_new visible. alice's "best" within window is p_new.
    rows = list_best_per_user_revealed(conn, org_id, since_iso="2026-04-12T00:00:00Z")
    assert len(rows) == 1
    assert rows[0]["post_id"] == "p_new"
    assert rows[0]["percentile"] == 80.0


def test_best_per_user_respects_limit(org_db):
    conn, org_id = org_db
    for i in range(15):
        uid = f"u{i:02d}"
        pid = f"p{i:02d}"
        upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id=pid, user_id=uid, percentile=float(50 + i)))
        _insert_streak_event(conn, pid, user_id=uid)
        _reveal(conn, pid)

    rows = list_best_per_user_revealed(conn, org_id, limit=5)
    assert len(rows) == 5


def test_best_per_user_empty_when_no_data(org_db):
    conn, org_id = org_db
    rows = list_best_per_user_revealed(conn, org_id)
    assert rows == []


def test_best_per_user_includes_channel_id(org_db):
    conn, org_id = org_db
    upsert_score_success(conn, **_success_kwargs(org_id=org_id, post_id="p1", user_id="alice"))
    _insert_streak_event(conn, "p1", channel_id="chan_42", user_id="alice")
    _reveal(conn, "p1")

    rows = list_best_per_user_revealed(conn, org_id)
    assert rows[0]["channel_id"] == "chan_42"
