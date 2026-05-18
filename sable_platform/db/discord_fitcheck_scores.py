"""DB helpers for discord_fitcheck_scores + image_phash on discord_streak_events.

Scored Mode V2 Pass A + B (migrations 049-050) and Pass C (migration 052).
Per `discord_streaks.py` / `discord_guild_config.py` precedent: positional
args after `conn`, named SQL params (`:foo`) with dict bindings (NOT qmark
`?`), explicit `conn.commit()` after writes, type-annotated `Connection`
param.

Surface:

* `set_phash_on_streak_event` — stamp pHash on a streak event (Pass A)
* `list_recent_phashes_for_collision` — fetch (user_id, post_id, image_phash)
  rows within a 90-day window. Hamming-distance check is app-side (SQLite
  has no popcount); the index covers candidate fetch.
* `upsert_score_success` — insert/upsert a successful scoring row
* `record_score_failure` — insert/upsert a failed scoring row (status='failed',
  score_error set, axis_* + percentile NULL)
* `get_score` — single-row read by (guild_id, post_id)
* `count_pool_size` — pool size for curve-basis decision (cold-start gate)
* `fetch_curve_pool_raw_totals` — raw_total values across the curve window,
  for percentile computation
* `invalidate_score` — mod-set invalidated_at + invalidated_reason

Pass C additions:

* `mark_reveal_fired` — one-and-done CAS that sets reveal_fired_at + post_id
  + trigger ONLY when reveal_fired_at IS NULL. Returns True if this caller
  won the lock, False if the row was already revealed (lost race or repeat).
  Callers pass `reveal_post_id='pending'` as a placeholder; the publish
  branch then either finalises via `update_reveal_post_id` or flips to a
  terminal failure trigger via `mark_reveal_publish_failed` /
  `convert_pending_to_cancelled_deleted`.
* `update_reveal_post_id` — guarded patch from 'pending' placeholder to
  the real Discord reply message id. Won't clobber a finalised reveal.
* `mark_reveal_publish_failed` — guarded conversion from 'pending' to
  the terminal 'publish_failed' trigger when Discord rejects the reply
  for non-404 reasons.
* `convert_pending_to_cancelled_deleted` — guarded conversion from 'pending'
  to 'cancelled_deleted' when the publish reply returns 404, i.e. the post
  was deleted DURING the reveal-publish window (one of the design-§6.4
  HIGH-severity gaming vectors that the in-process CAS race would otherwise
  swallow).
* `mark_reveal_cancelled_deleted` — same CAS shape but with trigger
  `cancelled_deleted` and no reveal_post_id. Permanently locks the row so
  a future Silent→Revealed flip can't accidentally re-fire on a tombstone.
* `record_emoji_milestone_crossing` — INSERT ... ON CONFLICT DO NOTHING on
  discord_fitcheck_emoji_milestones. Returns True if a new crossing row
  was inserted (caller should audit), False if the milestone was already
  recorded (no-op — bot restarts must NOT re-audit).
* `list_emoji_milestone_crossings_for_post` — diagnostic read of recorded
  crossings (mod-tool surface; not used by the live pipeline).

Leaderboard query notes (Pass D will own):
  Eligible reveal: `score_status='success' AND invalidated_at IS NULL
                    AND reveal_fired_at IS NOT NULL
                    AND reveal_trigger IN ('reactions','thread_messages')`
  The trigger filter is REQUIRED — `cancelled_deleted`, `publish_failed`,
  and `pending` rows ALL have non-NULL `reveal_fired_at` (the design uses
  that column as the one-and-done lock), so without the trigger filter the
  leaderboard would render junk entries.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


def _now_iso_seconds() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_dict(row: Any) -> dict:
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    return dict(row)


# ---------------------------------------------------------------------------
# Pass A — image_phash on discord_streak_events
# ---------------------------------------------------------------------------


def set_phash_on_streak_event(
    conn: Connection,
    guild_id: str,
    post_id: str,
    image_phash: str,
) -> bool:
    """Stamp image_phash on an existing streak event row.

    Returns True if a row matched and was updated, False if no such row
    exists (e.g. text post, deleted before phash compute). Does NOT
    overwrite an existing non-null phash — once-set is immutable per fit
    (the bytes never change). Safe to retry / re-call on the same post.
    """
    result = conn.execute(
        text(
            "UPDATE discord_streak_events"
            " SET image_phash = :phash, updated_at = :now"
            " WHERE guild_id = :guild_id AND post_id = :post_id"
            "   AND image_phash IS NULL"
        ),
        {
            "phash": image_phash,
            "now": _now_iso_seconds(),
            "guild_id": guild_id,
            "post_id": post_id,
        },
    )
    conn.commit()
    return result.rowcount == 1


def list_recent_phashes_for_collision(
    conn: Connection,
    org_id: str,
    since_iso: str,
    exclude_post_id: str | None = None,
) -> list[dict]:
    """Return non-null pHash rows from the last N days (since_iso UTC), same
    org. Hamming-distance bucketing is app-side; this returns candidates
    keyed only on (org_id, image_phash NOT NULL, created_at >= since_iso).

    exclude_post_id skips the just-inserted row so a fresh upsert doesn't
    collide with itself. Caller iterates + computes distance via imagehash.

    Bounded by 90d × posts/day × org — at SolStitch pre-launch this is
    ≤ ~100 rows; even at 100 fits/day the cap is ~9k rows / scan, well
    inside SQLite's hot-path comfort zone.
    """
    if exclude_post_id is None:
        rows = conn.execute(
            text(
                "SELECT post_id, user_id, image_phash, posted_at"
                " FROM discord_streak_events"
                " WHERE org_id = :org_id"
                "   AND image_phash IS NOT NULL"
                "   AND created_at >= :since"
                " ORDER BY created_at DESC"
            ),
            {"org_id": org_id, "since": since_iso},
        ).fetchall()
    else:
        rows = conn.execute(
            text(
                "SELECT post_id, user_id, image_phash, posted_at"
                " FROM discord_streak_events"
                " WHERE org_id = :org_id"
                "   AND image_phash IS NOT NULL"
                "   AND created_at >= :since"
                "   AND post_id != :exclude"
                " ORDER BY created_at DESC"
            ),
            {"org_id": org_id, "since": since_iso, "exclude": exclude_post_id},
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Pass B — discord_fitcheck_scores
# ---------------------------------------------------------------------------


def upsert_score_success(
    conn: Connection,
    org_id: str,
    guild_id: str,
    post_id: str,
    user_id: str,
    posted_at: str,
    scored_at: str,
    model_id: str,
    prompt_version: str,
    axis_cohesion: int,
    axis_execution: int,
    axis_concept: int,
    axis_catch: int,
    raw_total: int,
    catch_detected: str | None,
    catch_naming_class: str | None,
    description: str | None,
    confidence: float | None,
    axis_rationales_json: str | None,
    curve_basis: str,
    pool_size_at_score_time: int,
    percentile: float,
) -> None:
    """Insert/upsert a successful score row.

    ON CONFLICT (guild_id, post_id) DO UPDATE preserves the columns Pass C
    will own (reveal_eligible, reveal_fired_at, reveal_post_id,
    reveal_trigger) and the mod-only invalidation columns (invalidated_at,
    invalidated_reason). All other fields take excluded.* — re-scoring
    is rare but should reflect the new judgement.
    """
    conn.execute(
        text(
            "INSERT INTO discord_fitcheck_scores ("
            " org_id, guild_id, post_id, user_id, posted_at, scored_at,"
            " model_id, prompt_version, score_status, score_error,"
            " axis_cohesion, axis_execution, axis_concept, axis_catch,"
            " raw_total, catch_detected, catch_naming_class, description,"
            " confidence, axis_rationales_json, curve_basis,"
            " pool_size_at_score_time, percentile"
            ") VALUES ("
            " :org_id, :guild_id, :post_id, :user_id, :posted_at, :scored_at,"
            " :model_id, :prompt_version, 'success', NULL,"
            " :axis_cohesion, :axis_execution, :axis_concept, :axis_catch,"
            " :raw_total, :catch_detected, :catch_naming_class, :description,"
            " :confidence, :axis_rationales_json, :curve_basis,"
            " :pool_size_at_score_time, :percentile"
            ") ON CONFLICT (guild_id, post_id) DO UPDATE SET"
            "  scored_at = excluded.scored_at,"
            "  model_id = excluded.model_id,"
            "  prompt_version = excluded.prompt_version,"
            "  score_status = excluded.score_status,"
            "  score_error = excluded.score_error,"
            "  axis_cohesion = excluded.axis_cohesion,"
            "  axis_execution = excluded.axis_execution,"
            "  axis_concept = excluded.axis_concept,"
            "  axis_catch = excluded.axis_catch,"
            "  raw_total = excluded.raw_total,"
            "  catch_detected = excluded.catch_detected,"
            "  catch_naming_class = excluded.catch_naming_class,"
            "  description = excluded.description,"
            "  confidence = excluded.confidence,"
            "  axis_rationales_json = excluded.axis_rationales_json,"
            "  curve_basis = excluded.curve_basis,"
            "  pool_size_at_score_time = excluded.pool_size_at_score_time,"
            "  percentile = excluded.percentile,"
            "  updated_at = excluded.updated_at"
        ),
        {
            "org_id": org_id,
            "guild_id": guild_id,
            "post_id": post_id,
            "user_id": user_id,
            "posted_at": posted_at,
            "scored_at": scored_at,
            "model_id": model_id,
            "prompt_version": prompt_version,
            "axis_cohesion": axis_cohesion,
            "axis_execution": axis_execution,
            "axis_concept": axis_concept,
            "axis_catch": axis_catch,
            "raw_total": raw_total,
            "catch_detected": catch_detected,
            "catch_naming_class": catch_naming_class,
            "description": description,
            "confidence": confidence,
            "axis_rationales_json": axis_rationales_json,
            "curve_basis": curve_basis,
            "pool_size_at_score_time": pool_size_at_score_time,
            "percentile": percentile,
        },
    )
    conn.commit()


def record_score_failure(
    conn: Connection,
    org_id: str,
    guild_id: str,
    post_id: str,
    user_id: str,
    posted_at: str,
    scored_at: str,
    model_id: str,
    prompt_version: str,
    score_error: str,
) -> None:
    """Insert/upsert a failed score row. axis_* / percentile remain NULL.

    Distinct from upsert_score_success so callers can't accidentally pass
    NULL axes to a success path. Same ON CONFLICT semantics — preserves
    reveal_* and invalidated_* on collision. A retry that succeeds will
    overwrite this failure row via upsert_score_success.
    """
    conn.execute(
        text(
            "INSERT INTO discord_fitcheck_scores ("
            " org_id, guild_id, post_id, user_id, posted_at, scored_at,"
            " model_id, prompt_version, score_status, score_error"
            ") VALUES ("
            " :org_id, :guild_id, :post_id, :user_id, :posted_at, :scored_at,"
            " :model_id, :prompt_version, 'failed', :score_error"
            ") ON CONFLICT (guild_id, post_id) DO UPDATE SET"
            "  scored_at = excluded.scored_at,"
            "  model_id = excluded.model_id,"
            "  prompt_version = excluded.prompt_version,"
            "  score_status = excluded.score_status,"
            "  score_error = excluded.score_error,"
            "  updated_at = excluded.updated_at"
        ),
        {
            "org_id": org_id,
            "guild_id": guild_id,
            "post_id": post_id,
            "user_id": user_id,
            "posted_at": posted_at,
            "scored_at": scored_at,
            "model_id": model_id,
            "prompt_version": prompt_version,
            "score_error": score_error,
        },
    )
    conn.commit()


def get_score(conn: Connection, guild_id: str, post_id: str) -> dict | None:
    row = conn.execute(
        text(
            "SELECT * FROM discord_fitcheck_scores"
            " WHERE guild_id = :guild_id AND post_id = :post_id LIMIT 1"
        ),
        {"guild_id": guild_id, "post_id": post_id},
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def count_pool_size(
    conn: Connection,
    org_id: str,
    since_iso: str,
) -> int:
    """Count successful, non-invalidated scores in the curve window.

    Used pre-score to decide curve_basis ('absolute' below threshold,
    'rolling_30d' at/above). The window is computed by the caller
    (now - curve_window_days) so this stays a pure read.
    """
    row = conn.execute(
        text(
            "SELECT COUNT(*) AS n FROM discord_fitcheck_scores"
            " WHERE org_id = :org_id"
            "   AND score_status = 'success'"
            "   AND invalidated_at IS NULL"
            "   AND posted_at >= :since"
        ),
        {"org_id": org_id, "since": since_iso},
    ).fetchone()
    if row is None:
        return 0
    return int(row[0] if not hasattr(row, "_mapping") else row["n"])


def fetch_curve_pool_raw_totals(
    conn: Connection,
    org_id: str,
    since_iso: str,
) -> list[int]:
    """Return raw_total values (0-40) for the curve pool. Caller computes
    percentile via the standard rank/(n+1) formula or equivalent.

    Excludes failed + invalidated rows. Returns ints (raw_total NOT NULL
    for success rows -- success rows are written with raw_total as the
    sum of the four axes).
    """
    rows = conn.execute(
        text(
            "SELECT raw_total FROM discord_fitcheck_scores"
            " WHERE org_id = :org_id"
            "   AND score_status = 'success'"
            "   AND invalidated_at IS NULL"
            "   AND posted_at >= :since"
            "   AND raw_total IS NOT NULL"
        ),
        {"org_id": org_id, "since": since_iso},
    ).fetchall()
    return [int(r[0] if not hasattr(r, "_mapping") else r["raw_total"]) for r in rows]


def invalidate_score(
    conn: Connection,
    guild_id: str,
    post_id: str,
    reason: str,
) -> bool:
    """Mod-set invalidated_at + invalidated_reason. Returns True if a row
    was updated, False if no such post or it was already invalidated.
    """
    result = conn.execute(
        text(
            "UPDATE discord_fitcheck_scores"
            " SET invalidated_at = :now, invalidated_reason = :reason,"
            "  updated_at = :now"
            " WHERE guild_id = :guild_id AND post_id = :post_id"
            "   AND invalidated_at IS NULL"
        ),
        {
            "now": _now_iso_seconds(),
            "reason": reason,
            "guild_id": guild_id,
            "post_id": post_id,
        },
    )
    conn.commit()
    return result.rowcount == 1


# ---------------------------------------------------------------------------
# Pass C — reveal pipeline state
# ---------------------------------------------------------------------------


def mark_reveal_fired(
    conn: Connection,
    guild_id: str,
    post_id: str,
    reveal_post_id: str,
    reveal_trigger: str,
) -> bool:
    """One-and-done CAS: stamp reveal_fired_at + post_id + trigger ONLY if
    reveal_fired_at IS NULL on the row.

    Returns True if this caller won the lock (rowcount=1) — the only caller
    that should then write the public reveal post. False means the row was
    already revealed (concurrent fire, or post-restart double-fire attempt
    on the same post). Caller should NOT publish a reveal on False.

    `reveal_trigger` is the design-§3 enum: 'reactions' or 'thread_messages'.
    (See `mark_reveal_cancelled_deleted` for the cancellation variant.)
    """
    now = _now_iso_seconds()
    result = conn.execute(
        text(
            "UPDATE discord_fitcheck_scores"
            " SET reveal_fired_at = :now,"
            "     reveal_post_id = :reveal_post_id,"
            "     reveal_trigger = :reveal_trigger,"
            "     reveal_eligible = 1,"
            "     updated_at = :now"
            " WHERE guild_id = :guild_id AND post_id = :post_id"
            "   AND reveal_fired_at IS NULL"
        ),
        {
            "now": now,
            "reveal_post_id": reveal_post_id,
            "reveal_trigger": reveal_trigger,
            "guild_id": guild_id,
            "post_id": post_id,
        },
    )
    conn.commit()
    return result.rowcount == 1


def mark_reveal_cancelled_deleted(
    conn: Connection,
    guild_id: str,
    post_id: str,
) -> bool:
    """One-and-done CAS used when a fitcheck post is deleted before its
    reveal fires. Locks the row with reveal_trigger='cancelled_deleted'
    and no reveal_post_id so any future recompute (e.g. ghost reaction
    event after delete) sees a tripped one-and-done guard and bails.

    Returns True if this caller won the lock (the row had no reveal yet),
    False if the reveal already fired or another delete-handler already
    cancelled it. Caller writes the `fitcheck_reveal_cancelled_deleted`
    audit row only on True so we don't spam HIGH-severity audit rows.
    """
    now = _now_iso_seconds()
    result = conn.execute(
        text(
            "UPDATE discord_fitcheck_scores"
            " SET reveal_fired_at = :now,"
            "     reveal_post_id = NULL,"
            "     reveal_trigger = 'cancelled_deleted',"
            "     updated_at = :now"
            " WHERE guild_id = :guild_id AND post_id = :post_id"
            "   AND reveal_fired_at IS NULL"
        ),
        {
            "now": now,
            "guild_id": guild_id,
            "post_id": post_id,
        },
    )
    conn.commit()
    return result.rowcount == 1


def record_emoji_milestone_crossing(
    conn: Connection,
    org_id: str,
    guild_id: str,
    post_id: str,
    emoji_key: str,
    milestone: int,
) -> bool:
    """INSERT ... ON CONFLICT DO NOTHING on discord_fitcheck_emoji_milestones.

    `emoji_key` is the discord.py `str(reaction.emoji)` form — unicode
    glyph for stock emoji, `<:name:id>` / `<a:name:id>` for custom. Caller
    is responsible for using the same canonical form across calls;
    inconsistent keys partition the milestone state.

    `milestone` is one of the design-§7.3 levels (5 / 8 / 10) but the
    helper does not enforce the enum — caller decides. The UNIQUE
    constraint covers double-audit even under near-simultaneous reaction
    events from two recompute tasks (which shouldn't happen given the
    in-memory debounce, but: belt + suspenders for restart races).

    Returns True if a new row was inserted (caller writes the
    `fitcheck_reaction_milestone` audit), False if this exact crossing
    was already recorded (no audit — would be a duplicate row).
    """
    result = conn.execute(
        text(
            "INSERT INTO discord_fitcheck_emoji_milestones"
            " (org_id, guild_id, post_id, emoji_key, milestone, crossed_at)"
            " VALUES (:org_id, :guild_id, :post_id, :emoji_key, :milestone, :now)"
            " ON CONFLICT (guild_id, post_id, emoji_key, milestone) DO NOTHING"
        ),
        {
            "org_id": org_id,
            "guild_id": guild_id,
            "post_id": post_id,
            "emoji_key": emoji_key,
            "milestone": int(milestone),
            "now": _now_iso_seconds(),
        },
    )
    conn.commit()
    return result.rowcount == 1


def update_reveal_post_id(
    conn: Connection,
    guild_id: str,
    post_id: str,
    real_reveal_post_id: str,
) -> bool:
    """Patch reveal_post_id from the placeholder 'pending' to the real
    Discord message id of the published reveal. Guarded by
    `reveal_post_id = 'pending'` so an out-of-order caller (manual SQL,
    second replica) can't clobber a finalised reveal.

    Returns True if the row was patched (the placeholder was found and
    swapped), False if the placeholder wasn't present — either because
    the reveal already finalized, or because a publish-failure-flip already
    moved the trigger to 'publish_failed' / 'cancelled_deleted'.

    Defensive: rejects `'pending'` as a value to swap IN; the placeholder
    is reserved exclusively for the pre-publish lock and must never be
    accepted from a caller. (Discord snowflakes are 64-bit ints stringified;
    they can't naturally equal 'pending'.)
    """
    if real_reveal_post_id == "pending":
        raise ValueError(
            "real_reveal_post_id must NOT be the reserved 'pending' marker"
        )
    now = _now_iso_seconds()
    result = conn.execute(
        text(
            "UPDATE discord_fitcheck_scores"
            " SET reveal_post_id = :rid, updated_at = :now"
            " WHERE guild_id = :guild_id AND post_id = :post_id"
            "   AND reveal_post_id = 'pending'"
        ),
        {
            "rid": real_reveal_post_id,
            "now": now,
            "guild_id": guild_id,
            "post_id": post_id,
        },
    )
    conn.commit()
    return result.rowcount == 1


def mark_reveal_publish_failed(
    conn: Connection,
    guild_id: str,
    post_id: str,
) -> bool:
    """Convert a 'pending'-state lock to the terminal 'publish_failed' state
    when Discord reply HTTP fails for non-404 reasons.

    Guarded by `reveal_post_id = 'pending'` so we never overwrite a real
    reveal that's already been finalised. Returns True if the conversion
    landed. The caller writes the `fitcheck_reveal_publish_failed` audit
    row only on True.

    Distinction vs `mark_reveal_cancelled_deleted`: cancelled_deleted is
    the design's §6.4 HIGH-severity action for "post deleted before reveal
    fired", whereas publish_failed is "we held the lock, Discord rejected
    the reply, no retry". A 404 reply (post deleted between fetch and
    publish) should be classified by the caller as cancelled_deleted, not
    publish_failed — see the reveal_pipeline.handle_raw_message_delete +
    publish-error branch logic for the routing.
    """
    now = _now_iso_seconds()
    result = conn.execute(
        text(
            "UPDATE discord_fitcheck_scores"
            " SET reveal_post_id = NULL,"
            "     reveal_trigger = 'publish_failed',"
            "     updated_at = :now"
            " WHERE guild_id = :guild_id AND post_id = :post_id"
            "   AND reveal_post_id = 'pending'"
        ),
        {
            "now": now,
            "guild_id": guild_id,
            "post_id": post_id,
        },
    )
    conn.commit()
    return result.rowcount == 1


def convert_pending_to_cancelled_deleted(
    conn: Connection,
    guild_id: str,
    post_id: str,
) -> bool:
    """Convert a 'pending'-state lock to 'cancelled_deleted' when the
    publish attempt's Discord reply returns 404 — i.e. the post was deleted
    DURING the reveal-publish window. The CAS lock prevented the
    on_raw_message_delete handler from writing the design-§6.4 HIGH-
    severity audit (because reveal_fired_at was already non-NULL), so this
    helper lets the publish-error branch route to the correct audit class.

    Guarded by `reveal_post_id = 'pending'` so a finalised reveal is never
    relabelled. Returns True if the conversion landed.
    """
    now = _now_iso_seconds()
    result = conn.execute(
        text(
            "UPDATE discord_fitcheck_scores"
            " SET reveal_post_id = NULL,"
            "     reveal_trigger = 'cancelled_deleted',"
            "     updated_at = :now"
            " WHERE guild_id = :guild_id AND post_id = :post_id"
            "   AND reveal_post_id = 'pending'"
        ),
        {
            "now": now,
            "guild_id": guild_id,
            "post_id": post_id,
        },
    )
    conn.commit()
    return result.rowcount == 1


# ---------------------------------------------------------------------------
# Pass D — leaderboard queries
# ---------------------------------------------------------------------------


_LEADERBOARD_COLUMNS = (
    "s.org_id, s.guild_id, s.post_id, s.user_id, s.percentile,"
    " s.catch_detected, s.catch_naming_class,"
    " s.reveal_fired_at, s.reveal_trigger, s.posted_at,"
    " e.channel_id"
)

_LEADERBOARD_FROM = (
    " FROM discord_fitcheck_scores s"
    " LEFT JOIN discord_streak_events e"
    "   ON e.guild_id = s.guild_id AND e.post_id = s.post_id"
)

_LEADERBOARD_ELIGIBILITY = (
    " WHERE s.org_id = :org_id"
    "   AND s.score_status = 'success'"
    "   AND s.invalidated_at IS NULL"
    "   AND s.reveal_fired_at IS NOT NULL"
    "   AND s.reveal_trigger IN ('reactions', 'thread_messages')"
)


def list_top_revealed_fits(
    conn: Connection,
    org_id: str,
    *,
    since_iso: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Pass D `/leaderboard board:top_revealed` source.

    Returns the highest-percentile REVEALED fits for an org, one row per
    revealed post (the same user can appear multiple times — this is the
    "moments hall of fame" board, not the per-user ranking).

    Eligibility filter is the design-§6.4 contract: `reveal_fired_at IS
    NOT NULL` is necessary but not sufficient — the trigger filter is
    required to exclude `cancelled_deleted` / `publish_failed` rows that
    ALSO have a non-NULL `reveal_fired_at` (the column doubles as the
    one-and-done lock for terminal failure states). Without the trigger
    filter the leaderboard would render junk entries.

    `since_iso` (optional) filters by `reveal_fired_at >= :since` for the
    `window:30d` toggle. None → all-time.

    Tie-break: percentile DESC, reveal_fired_at DESC, post_id ASC. Stable
    ordering so the same query returns the same ranking even when scores
    tie.

    Returns dicts with channel_id from the streak event row so callers
    can build jump-links without a second query.
    """
    base = (
        "SELECT " + _LEADERBOARD_COLUMNS
        + _LEADERBOARD_FROM
        + _LEADERBOARD_ELIGIBILITY
    )
    params: dict[str, Any] = {"org_id": org_id, "limit": limit}
    if since_iso is not None:
        base += " AND s.reveal_fired_at >= :since"
        params["since"] = since_iso
    base += (
        " ORDER BY s.percentile DESC, s.reveal_fired_at DESC, s.post_id ASC"
        " LIMIT :limit"
    )
    rows = conn.execute(text(base), params).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_best_per_user_revealed(
    conn: Connection,
    org_id: str,
    *,
    since_iso: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Pass D `/leaderboard board:best_per_user` source.

    Same eligibility filter as `list_top_revealed_fits` but deduplicated
    to one row per `user_id` (each user's single best revealed fit). The
    "ranked players" board.

    Uses `ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY percentile DESC,
    reveal_fired_at DESC, post_id ASC)` and filters `rn = 1`. Window
    functions are SQLite-3.25+ (release 2018) so the same query runs on
    SQLite test fixtures and the Hetzner Postgres.
    """
    sql = (
        "WITH ranked AS ("
        " SELECT " + _LEADERBOARD_COLUMNS + ","
        "   ROW_NUMBER() OVER ("
        "     PARTITION BY s.user_id"
        "     ORDER BY s.percentile DESC, s.reveal_fired_at DESC, s.post_id ASC"
        "   ) AS rn"
        + _LEADERBOARD_FROM
        + _LEADERBOARD_ELIGIBILITY
    )
    params: dict[str, Any] = {"org_id": org_id, "limit": limit}
    if since_iso is not None:
        sql += " AND s.reveal_fired_at >= :since"
        params["since"] = since_iso
    sql += (
        ") SELECT * FROM ranked WHERE rn = 1"
        " ORDER BY percentile DESC, reveal_fired_at DESC, post_id ASC"
        " LIMIT :limit"
    )
    rows = conn.execute(text(sql), params).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_emoji_milestone_crossings_for_post(
    conn: Connection,
    guild_id: str,
    post_id: str,
) -> list[dict]:
    """Diagnostic read — return every milestone crossing for a post.

    Not used by the live reveal pipeline (the bot just calls
    `record_emoji_milestone_crossing` and relies on the boolean return);
    surfaces here so mods + Pass D leaderboard tools can query historical
    crossing state without raw SQL.
    """
    rows = conn.execute(
        text(
            "SELECT emoji_key, milestone, crossed_at"
            " FROM discord_fitcheck_emoji_milestones"
            " WHERE guild_id = :guild_id AND post_id = :post_id"
            " ORDER BY milestone ASC, emoji_key ASC"
        ),
        {"guild_id": guild_id, "post_id": post_id},
    ).fetchall()
    return [_row_to_dict(r) for r in rows]
