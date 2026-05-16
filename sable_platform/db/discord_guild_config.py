"""DB helpers for discord_guild_config in sable.db.

Per-guild config for sable-roles V2 features:
- relax_mode_on: when 1, the bot relaxes #fitcheck enforcement (no delete+DM,
  no auto-threading). When 0, normal enforcement.
- current_burn_mode: 'once', 'persist', or 'never'. Global default mode applied to
  /burn-me opt-ins (V2 burn-me feature).
- personalize_mode_on: when 1, /roast generation injects a per-target
  user_vibe block AND the weekly vibe-inference cron runs for this guild.
  When 0, observation pipeline still accumulates (cheap) but no LLM calls.
  Default 0 — guilds explicitly opt in via /set-personalize-mode (mig 047).

Lazily-created: rows are inserted by the first mod toggle. Reads return
defaults (relax_mode_on=0, current_burn_mode='once', personalize_mode_on=0)
if no row exists.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.db.audit import log_audit

VALID_BURN_MODES = ("once", "persist", "never")


def _now_iso_seconds() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_config(conn: Connection, guild_id: str) -> dict:
    """Read the config row for a guild. Returns defaults if no row exists.

    Returns a dict with keys: guild_id, relax_mode_on (int 0|1),
    current_burn_mode (str), personalize_mode_on (int 0|1),
    updated_at (str|None), updated_by (str|None).
    For unconfigured guilds, updated_at and updated_by are None.
    """
    row = conn.execute(
        text(
            "SELECT guild_id, relax_mode_on, current_burn_mode,"
            " personalize_mode_on, updated_at, updated_by"
            " FROM discord_guild_config WHERE guild_id = :guild_id LIMIT 1"
        ),
        {"guild_id": guild_id},
    ).fetchone()
    if row is None:
        return {
            "guild_id": guild_id,
            "relax_mode_on": 0,
            "current_burn_mode": "once",
            "personalize_mode_on": 0,
            "updated_at": None,
            "updated_by": None,
        }
    return {
        "guild_id": row["guild_id"],
        "relax_mode_on": int(row["relax_mode_on"]),
        "current_burn_mode": row["current_burn_mode"],
        "personalize_mode_on": int(row["personalize_mode_on"]),
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
    }


def set_relax_mode(
    conn: Connection,
    guild_id: str,
    on: bool,
    updated_by: str,
) -> None:
    """Upsert relax_mode_on for a guild. On conflict, only relax_mode_on +
    updated_at + updated_by change; current_burn_mode is preserved.
    """
    now = _now_iso_seconds()
    conn.execute(
        text(
            "INSERT INTO discord_guild_config"
            " (guild_id, relax_mode_on, updated_at, updated_by)"
            " VALUES (:guild_id, :relax, :now, :by)"
            " ON CONFLICT (guild_id) DO UPDATE SET"
            "  relax_mode_on = excluded.relax_mode_on,"
            "  updated_at = excluded.updated_at,"
            "  updated_by = excluded.updated_by"
        ),
        {
            "guild_id": guild_id,
            "relax": 1 if on else 0,
            "now": now,
            "by": updated_by,
        },
    )
    conn.commit()


def set_personalize_mode(
    conn: Connection,
    *,
    guild_id: str,
    on: bool,
    updated_by: str,
) -> dict:
    """Upsert personalize_mode_on for a guild + audit inside the same txn.

    On conflict, only personalize_mode_on + updated_at + updated_by change;
    relax_mode_on and current_burn_mode are preserved.

    Audit-inside-helper convention (vs set_burn_mode / set_relax_mode where
    the caller writes the audit row) so /peer-roast-report in R9 can grep
    `fitcheck_personalize_mode_set` rows from audit_log alone without
    chasing per-feature audit-emit sites. Detail dict shape locked by
    plan §0.3; do not add fields without grepping R9 first.

    Gates the per-target user_vibe injection in generate_roast and the
    weekly vibe-inference cron. Observation pipeline runs regardless.

    Returns the resulting config row (per get_config shape) so callers
    can confirm + render the new state without a second read.
    """
    now = _now_iso_seconds()
    conn.execute(
        text(
            "INSERT INTO discord_guild_config"
            " (guild_id, personalize_mode_on, updated_at, updated_by)"
            " VALUES (:guild_id, :personalize, :now, :by)"
            " ON CONFLICT (guild_id) DO UPDATE SET"
            "  personalize_mode_on = excluded.personalize_mode_on,"
            "  updated_at = excluded.updated_at,"
            "  updated_by = excluded.updated_by"
        ),
        {
            "guild_id": guild_id,
            "personalize": 1 if on else 0,
            "now": now,
            "by": updated_by,
        },
    )
    # log_audit commits the txn, flushing the upsert + audit row together.
    log_audit(
        conn,
        actor=f"discord:user:{updated_by}",
        action="fitcheck_personalize_mode_set",
        org_id=None,
        entity_id=None,
        detail={
            "on": bool(on),
            "guild_id": guild_id,
            "updated_by": updated_by,
        },
        source="sable-roles",
    )
    return get_config(conn, guild_id)


def set_burn_mode(
    conn: Connection,
    guild_id: str,
    mode: str,
    updated_by: str,
) -> None:
    """Upsert current_burn_mode for a guild. On conflict, only current_burn_mode
    + updated_at + updated_by change; relax_mode_on is preserved.

    Reserved for V2 /burn-me build. Lands now so the schema + helper interface
    are stable when burn-me ships.
    """
    if mode not in VALID_BURN_MODES:
        raise ValueError(f"mode must be one of {VALID_BURN_MODES}, got {mode!r}")
    now = _now_iso_seconds()
    conn.execute(
        text(
            "INSERT INTO discord_guild_config"
            " (guild_id, current_burn_mode, updated_at, updated_by)"
            " VALUES (:guild_id, :mode, :now, :by)"
            " ON CONFLICT (guild_id) DO UPDATE SET"
            "  current_burn_mode = excluded.current_burn_mode,"
            "  updated_at = excluded.updated_at,"
            "  updated_by = excluded.updated_by"
        ),
        {
            "guild_id": guild_id,
            "mode": mode,
            "now": now,
            "by": updated_by,
        },
    )
    conn.commit()
