"""DB helpers for discord_scoring_config in sable.db.

Scored Mode V2 Pass B (migration 051). Per-guild state machine + tunables.

Default state = 'off' — safety floor. Reads return defaults if no row exists
(state='off', plan-§6.3 thresholds, model='claude-sonnet-4-6').

Audit-inside-helper convention mirrors `discord_guild_config.set_personalize_mode`
(commit + audit in the same txn) so any downstream surface (alerts,
dashboards, /scoring suspicious in V2) can grep
`fitcheck_scoring_state_changed` from `audit_log` alone without chasing
per-feature audit-emit sites.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.db.audit import log_audit

VALID_STATES = ("off", "silent", "revealed")


def _now_iso_seconds() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_dict(row) -> dict:
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    return dict(row)


_DEFAULT_CONFIG = {
    "state": "off",
    "state_changed_by": None,
    "state_changed_at": None,
    "reaction_threshold": 10,
    "thread_message_threshold": 100,
    "reveal_window_days": 7,
    "reveal_min_age_minutes": 10,
    "curve_window_days": 30,
    "cold_start_min_pool": 20,
    "model_id": "claude-sonnet-4-6",
    "prompt_version": "rubric_v1",
}


def get_config(conn: Connection, guild_id: str) -> dict:
    """Read the scoring config for a guild. Returns defaults (state='off',
    plan-§6.3 thresholds) when no row exists.

    Returns a dict with keys: guild_id, org_id (None if unconfigured),
    state, state_changed_by, state_changed_at, reaction_threshold,
    thread_message_threshold, reveal_window_days, reveal_min_age_minutes,
    curve_window_days, cold_start_min_pool, model_id, prompt_version.
    """
    row = conn.execute(
        text(
            "SELECT org_id, guild_id, state, state_changed_by, state_changed_at,"
            " reaction_threshold, thread_message_threshold, reveal_window_days,"
            " reveal_min_age_minutes, curve_window_days, cold_start_min_pool,"
            " model_id, prompt_version"
            " FROM discord_scoring_config WHERE guild_id = :guild_id LIMIT 1"
        ),
        {"guild_id": guild_id},
    ).fetchone()
    if row is None:
        out = dict(_DEFAULT_CONFIG)
        out["org_id"] = None
        out["guild_id"] = guild_id
        return out
    return _row_to_dict(row)


def set_state(
    conn: Connection,
    *,
    org_id: str,
    guild_id: str,
    state: str,
    updated_by: str,
) -> dict:
    """Upsert state + state_changed_{by,at} for a guild. Writes audit row
    `fitcheck_scoring_state_changed` inside the same txn (log_audit commits).

    Validates `state` in VALID_STATES; raises ValueError otherwise. Caller
    is the slash-command handler, which has already gatekept on Manage
    Guild permission — this is a defense-in-depth check.

    Returns the resulting config row (per get_config shape) so the caller
    can confirm + render the new state without a second read.
    """
    if state not in VALID_STATES:
        raise ValueError(f"state must be one of {VALID_STATES}, got {state!r}")
    now = _now_iso_seconds()
    # Read prior state for audit detail (before the upsert overwrites it).
    prior = get_config(conn, guild_id)
    prior_state = prior["state"]

    conn.execute(
        text(
            "INSERT INTO discord_scoring_config"
            " (org_id, guild_id, state, state_changed_by, state_changed_at, updated_at)"
            " VALUES (:org_id, :guild_id, :state, :by, :now, :now)"
            " ON CONFLICT (guild_id) DO UPDATE SET"
            "  state = excluded.state,"
            "  state_changed_by = excluded.state_changed_by,"
            "  state_changed_at = excluded.state_changed_at,"
            "  updated_at = excluded.updated_at"
        ),
        {
            "org_id": org_id,
            "guild_id": guild_id,
            "state": state,
            "by": updated_by,
            "now": now,
        },
    )
    log_audit(
        conn,
        actor=f"discord:user:{updated_by}",
        action="fitcheck_scoring_state_changed",
        org_id=org_id,
        entity_id=None,
        detail={
            "guild_id": guild_id,
            "prior_state": prior_state,
            "new_state": state,
            "updated_by": updated_by,
        },
        source="sable-roles",
    )
    return get_config(conn, guild_id)


def count_status_breakdown(
    conn: Connection,
    org_id: str,
    guild_id: str,
) -> dict[str, int]:
    """Return {'success': N, 'failed': M, 'total': N+M, 'invalidated': X}.

    Used by /scoring status — caller renders the breakdown in the ephemeral
    response. Counts across all time so the historical pool is visible
    (cold-start gate uses count_pool_size on a fresh time window).
    """
    rows = conn.execute(
        text(
            "SELECT score_status, COUNT(*) AS n FROM discord_fitcheck_scores"
            " WHERE org_id = :org_id AND guild_id = :guild_id"
            " GROUP BY score_status"
        ),
        {"org_id": org_id, "guild_id": guild_id},
    ).fetchall()
    out = {"success": 0, "failed": 0, "total": 0, "invalidated": 0}
    for r in rows:
        d = _row_to_dict(r)
        status = d["score_status"]
        n = int(d["n"])
        if status in out:
            out[status] = n
        out["total"] += n
    inv_row = conn.execute(
        text(
            "SELECT COUNT(*) AS n FROM discord_fitcheck_scores"
            " WHERE org_id = :org_id AND guild_id = :guild_id"
            "   AND invalidated_at IS NOT NULL"
        ),
        {"org_id": org_id, "guild_id": guild_id},
    ).fetchone()
    if inv_row is not None:
        out["invalidated"] = int(
            inv_row[0] if not hasattr(inv_row, "_mapping") else inv_row["n"]
        )
    return out
