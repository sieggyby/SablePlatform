"""DB helpers for discord_fitcheck_scores + image_phash on discord_streak_events.

Scored Mode V2 Pass A + B (migrations 049-050). Per `discord_streaks.py` /
`discord_guild_config.py` precedent: positional args after `conn`, named SQL
params (`:foo`) with dict bindings (NOT qmark `?`), explicit `conn.commit()`
after writes, type-annotated `Connection` param.

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
